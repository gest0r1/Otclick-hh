#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${OTCLICK_REPO_URL:-https://github.com/gest0r1/Otclick-hh.git}"
BRANCH="${OTCLICK_BRANCH:-main}"
DEFAULT_TARGET_DIR="/opt/otclick-hh"
RELEASE_REPO="${OTCLICK_RELEASE_REPO:-gest0r1/Otclick-hh}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

if [[ -n "${1:-}" ]]; then
  TARGET_DIR="$1"
elif [[ -n "${OTCLICK_DIR:-}" ]]; then
  TARGET_DIR="$OTCLICK_DIR"
else
  TARGET_DIR="$DEFAULT_TARGET_DIR"
fi

need git
need python3
need curl
need gzip
need sha256sum
need docker
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose)."

case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "Prebuilt production images currently support Linux amd64 only; detected $(uname -m)." ;;
esac

if [[ -d "$TARGET_DIR/.git" ]]; then
  log "Updating Otclick in $TARGET_DIR"

  if ! git -C "$TARGET_DIR" diff --quiet || ! git -C "$TARGET_DIR" diff --cached --quiet; then
    die "Tracked local changes found in $TARGET_DIR. Commit/stash them before updating."
  fi

  git -C "$TARGET_DIR" remote set-url origin "$REPO_URL"
  git -C "$TARGET_DIR" fetch --prune origin "$BRANCH"

  if git -C "$TARGET_DIR" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$TARGET_DIR" checkout "$BRANCH"
  else
    git -C "$TARGET_DIR" checkout -b "$BRANCH" --track "origin/$BRANCH"
  fi

  git -C "$TARGET_DIR" merge --ff-only "origin/$BRANCH"
elif [[ -e "$TARGET_DIR" ]] && [[ -n "$(ls -A "$TARGET_DIR" 2>/dev/null || true)" ]]; then
  die "Target directory exists and is not an Otclick git checkout: $TARGET_DIR"
else
  log "Installing Otclick into $TARGET_DIR"
  mkdir -p "$(dirname "$TARGET_DIR")"
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"

existing_database_detected() {
  docker container inspect aiautoclicker-db >/dev/null 2>&1 && return 0
  docker volume ls --format '{{.Name}}' 2>/dev/null | grep -Eq '(^|[_-])supabase-db-data$'
}

if [[ ! -f .env ]]; then
  if existing_database_detected; then
    die "Existing Otclick PostgreSQL data was detected, but $TARGET_DIR/.env is missing. Refusing to generate new PostgreSQL/JWT/Fernet secrets. Restore the original .env before continuing."
  fi

  log "Creating .env with generated secrets"
  python3 infra/bootstrap.py --openai-key ""
  printf '\nOPENAI_API_KEY is empty. Add your OpenAI-compatible key to %s/.env when needed.\n' "$TARGET_DIR"
else
  log "Keeping existing .env unchanged"
fi

[[ -f docker-compose.prebuilt.yml ]] || die "docker-compose.prebuilt.yml is missing from $TARGET_DIR"
[[ -f infra/frontend-runtime-env.sh ]] || die "infra/frontend-runtime-env.sh is missing from $TARGET_DIR"

load_prebuilt_images() {
  local git_sha release_tag release_base tmpdir manifest_sha ready attempt
  git_sha="$(git rev-parse HEAD)"
  release_tag="install-${git_sha}"
  release_base="https://github.com/${RELEASE_REPO}/releases/download/${release_tag}"
  tmpdir="$(mktemp -d)"

  log "Waiting for GitHub Actions prebuilt images for ${git_sha:0:12}"
  ready=0
  for attempt in $(seq 1 180); do
    if curl -fsSL --connect-timeout 10 --max-time 30 \
      "${release_base}/manifest.json" -o "$tmpdir/manifest.json"; then
      ready=1
      break
    fi
    if (( attempt % 12 == 0 )); then
      printf '    artifact is still being built in GitHub Actions...\n'
    fi
    sleep 5
  done
  if [[ "$ready" != "1" ]]; then
    rm -rf "$tmpdir"
    die "Prebuilt release ${release_tag} was not published. Server-side Docker build is intentionally disabled. Check GitHub Actions build-artifact workflow."
  fi

  manifest_sha="$(python3 - "$tmpdir/manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    print(json.load(fh).get("git_sha", ""))
PY
)"
  if [[ "$manifest_sha" != "$git_sha" ]]; then
    rm -rf "$tmpdir"
    die "Artifact SHA mismatch: manifest=$manifest_sha checkout=$git_sha"
  fi

  log "Downloading prebuilt backend/frontend images"
  curl -fL --retry 5 --retry-delay 3 --retry-all-errors \
    "${release_base}/SHA256SUMS" -o "$tmpdir/SHA256SUMS"
  curl -fL --retry 5 --retry-delay 3 --retry-all-errors \
    "${release_base}/otclick-images-linux-amd64.tar.gz" \
    -o "$tmpdir/otclick-images-linux-amd64.tar.gz"

  (
    cd "$tmpdir"
    sha256sum -c SHA256SUMS
  )

  log "Loading verified application images into Docker"
  gzip -dc "$tmpdir/otclick-images-linux-amd64.tar.gz" | docker load
  rm -rf "$tmpdir"

  docker image inspect aiautoclicker-backend:latest >/dev/null 2>&1 \
    || die "Prebuilt backend image was not loaded"
  docker image inspect aiautoclicker-frontend:latest >/dev/null 2>&1 \
    || die "Prebuilt frontend image was not loaded"
}

compose() {
  docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml "$@"
}

wait_migrate() {
  local id state exit_code attempt
  for attempt in $(seq 1 90); do
    id="$(compose ps -a -q migrate 2>/dev/null || true)"
    if [[ -n "$id" ]]; then
      state="$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || true)"
      exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$id" 2>/dev/null || true)"
      if [[ "$state" == "exited" && "$exit_code" == "0" ]]; then
        return 0
      fi
      if [[ "$state" == "exited" && "$exit_code" != "0" ]]; then
        compose logs --no-color --tail=200 migrate >&2 || true
        die "Database migrations failed with exit code $exit_code"
      fi
    fi
    sleep 2
  done
  compose logs --no-color --tail=200 migrate >&2 || true
  die "Database migrations did not finish"
}

load_prebuilt_images

log "Pulling third-party infrastructure images"
compose pull db migrate auth rest realtime storage storage-init kong

log "Starting infrastructure and applying migrations (no local build)"
compose up -d --no-build --pull never db migrate auth rest realtime storage storage-init kong
wait_migrate

log "Starting application from prebuilt images (no local build)"
compose up -d --no-build --pull never --force-recreate api frontend worker

log "Stack status"
compose ps -a

printf '\nOtclick is installed/updated in: %s\n' "$TARGET_DIR"
printf 'Application images: prebuilt by GitHub Actions; local Docker build: disabled.\n'
printf 'Open: http://localhost:3000\n'
printf 'Run this same installer command again to update to the latest main.\n'
