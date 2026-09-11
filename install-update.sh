#!/usr/bin/env bash
set -Eeuo pipefail

# Incremental production updater for an existing Otclick installation.
# It downloads only a tiny exact-SHA manifest, then pulls changed application
# components from GHCR so Docker can reuse cached layers. GitHub Release
# component archives are a fallback only; the combined fresh-install bundle is
# never downloaded by this script.
#
# Important: an already-up-to-date checkout still runs the runtime reconciliation
# path. This makes the same one-command installer usable as a repair command after
# an interrupted deployment or a stopped/crashed application container.

REPO_SLUG="${OTCLICK_REPO_SLUG:-gest0r1/Otclick-hh}"
REF="${OTCLICK_REF:-main}"
INSTALL_DIR="${OTCLICK_DIR:-/opt/otclick-hh}"
LOG_DIR="${OTCLICK_LOG_DIR:-/var/log/otclick-hh}"
STATE_DIR="${OTCLICK_STATE_DIR:-/var/lib/otclick-hh}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/update-$STAMP.log"
MANIFEST_FILE=""
SUMS_FILE=""
BACKUP_FILE=""

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (use sudo)." >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$STATE_DIR"
chmod 700 "$LOG_DIR" "$STATE_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[otclick] %s\n' "$*"; }
die() { printf '[otclick] ERROR: %s\n' "$*" >&2; exit 1; }

on_error() {
  local code=$?
  trap - ERR
  echo
  echo "[otclick] incremental update failed (exit $code)."
  if [[ -n "$BACKUP_FILE" ]]; then
    echo "Database backup: $BACKUP_FILE"
  fi
  echo "Log: $LOG_FILE"
  exit "$code"
}
trap on_error ERR

# EXIT traps must always return 0. The previous form used `[[ ... ]] && rm ...`;
# with empty temp-file variables the last test returned 1 and incorrectly fired
# the ERR trap even after a successful `exit 0` on an up-to-date checkout.
cleanup() {
  if [[ -n "$MANIFEST_FILE" ]]; then
    rm -f "$MANIFEST_FILE" || true
  fi
  if [[ -n "$SUMS_FILE" ]]; then
    rm -f "$SUMS_FILE" || true
  fi
  return 0
}
trap cleanup EXIT

for tool in git curl python3 sha256sum docker; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool is missing: $tool"
done
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
[[ -d "$INSTALL_DIR/.git" ]] || die "existing installation not found at $INSTALL_DIR; use install.sh for a fresh install"
[[ -f "$INSTALL_DIR/.env" ]] || die "$INSTALL_DIR/.env is missing; restore the original .env before updating"

cd "$INSTALL_DIR"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  log "tracked local changes that block update:"
  git status --short --untracked-files=no | sed 's/^/[otclick]   /'
  die "commit/stash tracked changes before update"
fi

compose() {
  docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml "$@"
}

component_hash() {
  local component="$1"
  python3 - "$component" <<'PY'
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

component = sys.argv[1]
pathspecs = {
    "backend": [
        ".dockerignore",
        "backend/Dockerfile",
        "backend/app",
        "backend/worker_main.py",
        "backend/data",
        "backend/scripts",
        "pyproject.toml",
        "uv.lock",
    ],
    "frontend": ["frontend"],
    "compose": ["docker-compose.yml", "docker-compose.prebuilt.yml"],
    "infra": ["infra"],
    "migrations": ["infra/supabase/migrations"],
}
if component not in pathspecs:
    raise SystemExit(f"unknown component: {component}")
raw = subprocess.check_output(["git", "ls-files", "-z", "--", *pathspecs[component]])
files = sorted({p.decode("utf-8") for p in raw.split(b"\0") if p})
h = hashlib.sha256()
for rel in files:
    path = Path(rel)
    if not path.is_file():
        continue
    h.update(rel.encode("utf-8"))
    h.update(b"\0")
    h.update(path.read_bytes())
    h.update(b"\0")
print(h.hexdigest())
PY
}

wait_http() {
  local url="$1" label="$2" timeout="${3:-90}" i
  for ((i=1; i<=timeout; i++)); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      log "      $label healthy"
      return 0
    fi
    sleep 1
  done
  return 1
}

runtime_diagnostics() {
  local service="$1"
  echo >&2
  echo "[otclick] runtime diagnostics for $service:" >&2
  compose ps -a >&2 || true
  echo >&2
  compose logs --no-color --tail=200 "$service" >&2 || true
}

require_http() {
  local url="$1" label="$2" service="$3" timeout="${4:-90}"
  if wait_http "$url" "$label" "$timeout"; then
    return 0
  fi
  runtime_diagnostics "$service"
  die "$label did not become healthy: $url"
}

wait_migrate() {
  local id state exit_code i
  for ((i=1; i<=90; i++)); do
    id="$(compose ps -aq migrate 2>/dev/null | head -n1)"
    if [[ -n "$id" ]]; then
      state="$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || true)"
      if [[ "$state" == "exited" ]]; then
        exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$id" 2>/dev/null || true)"
        if [[ "$exit_code" == "0" ]]; then
          log "      migrations complete"
          return 0
        fi
        runtime_diagnostics migrate
        die "database migrations failed (exit ${exit_code:-unknown})"
      fi
    fi
    sleep 2
  done
  runtime_diagnostics migrate
  die "database migrations did not finish"
}

backup_database() {
  mkdir -p backups
  chmod 700 backups
  local db_id db_health i tmp
  db_id="$(compose ps -q db 2>/dev/null || true)"
  if [[ -z "$db_id" || "$(docker inspect -f '{{.State.Running}}' "$db_id" 2>/dev/null || true)" != "true" ]]; then
    log "      starting DB for migration backup"
    compose up -d --no-build --pull never db >>"$LOG_FILE" 2>&1
    for ((i=1; i<=60; i++)); do
      db_id="$(compose ps -q db 2>/dev/null || true)"
      db_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$db_id" 2>/dev/null || true)"
      [[ "$db_health" == "healthy" || "$db_health" == "running" ]] && break
      sleep 2
    done
  fi
  [[ -n "$db_id" ]] || die "database container unavailable for backup"
  BACKUP_FILE="$INSTALL_DIR/backups/postgres-$STAMP.dump"
  tmp="$BACKUP_FILE.tmp"
  log "      creating DB backup before schema migration"
  rm -f "$tmp"
  compose exec -T db pg_dump -U postgres -d postgres -Fc >"$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$BACKUP_FILE"
}

ensure_zstd() {
  command -v zstd >/dev/null 2>&1 && return 0
  if command -v apt-get >/dev/null 2>&1; then
    log "      installing zstd for Release fallback"
    apt-get update -qq >>"$LOG_FILE" 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd >>"$LOG_FILE" 2>&1
  fi
  command -v zstd >/dev/null 2>&1 || die "zstd is required only for the GitHub Release fallback, but could not be installed"
}

image_matches_target() {
  local target_ref="$1" local_tag="$2" target_id local_id
  # A locally built/stale :latest image is not proof that it matches the exact
  # content-addressed artifact. The exact digest itself must exist locally and
  # resolve to the same image ID as the runtime tag.
  docker image inspect "$target_ref" >/dev/null 2>&1 || return 1
  docker image inspect "$local_tag" >/dev/null 2>&1 || return 1
  target_id="$(docker image inspect "$target_ref" --format '{{.Id}}')"
  local_id="$(docker image inspect "$local_tag" --format '{{.Id}}')"
  [[ -n "$target_id" && "$target_id" == "$local_id" ]]
}

OLD_SHA="$(git rev-parse HEAD)"
OLD_BACKEND_HASH="$(component_hash backend)"
OLD_FRONTEND_HASH="$(component_hash frontend)"
OLD_COMPOSE_HASH="$(component_hash compose)"
OLD_INFRA_HASH="$(component_hash infra)"
OLD_MIGRATIONS_HASH="$(component_hash migrations)"

log "[1/7] checking repository head"
git fetch --prune origin "$REF" >>"$LOG_FILE" 2>&1
TARGET_SHA="$(git rev-parse "origin/$REF")"
if [[ "$TARGET_SHA" == "$OLD_SHA" && "${OTCLICK_FORCE_UPDATE:-0}" != "1" ]]; then
  log "      code already up to date: $TARGET_SHA; continuing with artifact/runtime verification"
fi

log "[2/7] fetching exact-SHA incremental manifest"
release_tag="install-${TARGET_SHA}"
release_base="https://github.com/${REPO_SLUG}/releases/download/${release_tag}"
MANIFEST_FILE="$(mktemp /tmp/otclick-manifest.XXXXXX.json)"
SUMS_FILE="$(mktemp /tmp/otclick-sums.XXXXXX)"

ready=0
for attempt in $(seq 1 36); do
  if curl -fsSL --retry 2 --retry-delay 1 "${release_base}/manifest.json" -o "$MANIFEST_FILE" \
     && curl -fsSL --retry 2 --retry-delay 1 "${release_base}/SHA256SUMS" -o "$SUMS_FILE"; then
    ready=1
    break
  fi
  [[ "$attempt" -eq 36 ]] && break
  [[ $((attempt % 6)) -eq 0 ]] && log "      artifact is still being built in GitHub Actions..."
  sleep 10
done
[[ "$ready" == "1" ]] || die "prebuilt manifest is not ready for ${TARGET_SHA}"

expected_manifest="$(awk '$2 == "manifest.json" || $2 == "./manifest.json" {print $1; exit}' "$SUMS_FILE")"
[[ -n "$expected_manifest" ]] || die "manifest checksum is missing"
actual_manifest="$(sha256sum "$MANIFEST_FILE" | awk '{print $1}')"
[[ "$actual_manifest" == "$expected_manifest" ]] || die "manifest checksum mismatch"

mapfile -t META < <(python3 - "$MANIFEST_FILE" "$TARGET_SHA" <<'PY'
import json
import sys

path, target_sha = sys.argv[1:]
data = json.load(open(path, encoding="utf-8"))
if data.get("schema_version") != 2:
    raise SystemExit("unsupported manifest schema")
if data.get("git_sha") != target_sha:
    raise SystemExit("manifest git_sha mismatch")
if data.get("architecture") != "linux/amd64":
    raise SystemExit("unsupported manifest architecture")
components = data["components"]
hashes = data["hashes"]
for key in ("backend", "frontend"):
    comp = components[key]
    for field in ("hash", "image", "fallback_tag", "fallback_asset"):
        if not comp.get(field):
            raise SystemExit(f"manifest missing {key}.{field}")
print(components["backend"]["hash"])
print(components["backend"]["image"])
print(components["backend"]["fallback_tag"])
print(components["backend"]["fallback_asset"])
print(components["frontend"]["hash"])
print(components["frontend"]["image"])
print(components["frontend"]["fallback_tag"])
print(components["frontend"]["fallback_asset"])
print(hashes["compose"])
print(hashes["infra"])
print(hashes["migrations"])
PY
)
[[ "${#META[@]}" -eq 11 ]] || die "invalid manifest metadata"

TARGET_BACKEND_HASH="${META[0]}"
TARGET_BACKEND_IMAGE="${META[1]}"
TARGET_BACKEND_FALLBACK_TAG="${META[2]}"
TARGET_BACKEND_FALLBACK_ASSET="${META[3]}"
TARGET_FRONTEND_HASH="${META[4]}"
TARGET_FRONTEND_IMAGE="${META[5]}"
TARGET_FRONTEND_FALLBACK_TAG="${META[6]}"
TARGET_FRONTEND_FALLBACK_ASSET="${META[7]}"
TARGET_COMPOSE_HASH="${META[8]}"
TARGET_INFRA_HASH="${META[9]}"
TARGET_MIGRATIONS_HASH="${META[10]}"

BACKEND_CHANGED=0
FRONTEND_CHANGED=0
COMPOSE_CHANGED=0
INFRA_CHANGED=0
MIGRATIONS_CHANGED=0
[[ "$OLD_BACKEND_HASH" != "$TARGET_BACKEND_HASH" ]] && BACKEND_CHANGED=1
[[ "$OLD_FRONTEND_HASH" != "$TARGET_FRONTEND_HASH" ]] && FRONTEND_CHANGED=1
[[ "$OLD_COMPOSE_HASH" != "$TARGET_COMPOSE_HASH" ]] && COMPOSE_CHANGED=1
[[ "$OLD_INFRA_HASH" != "$TARGET_INFRA_HASH" ]] && INFRA_CHANGED=1
[[ "$OLD_MIGRATIONS_HASH" != "$TARGET_MIGRATIONS_HASH" ]] && MIGRATIONS_CHANGED=1

# Do not trust a pre-existing :latest tag merely because the source hash did not
# change. The interrupted migration from the old installer can leave an older
# locally-built image under the same tag. Require the exact target digest.
image_matches_target "$TARGET_BACKEND_IMAGE" aiautoclicker-backend:latest || BACKEND_CHANGED=1
image_matches_target "$TARGET_FRONTEND_IMAGE" aiautoclicker-frontend:latest || FRONTEND_CHANGED=1

log "      changed/required: backend=$BACKEND_CHANGED frontend=$FRONTEND_CHANGED compose=$COMPOSE_CHANGED infra=$INFRA_CHANGED migrations=$MIGRATIONS_CHANGED"

pull_component() {
  local name="$1" image_ref="$2" fallback_tag="$3" fallback_asset="$4" local_tag="$5"
  local fallback_base fallback_sums fallback_file expected actual

  log "      $name: pulling immutable image from GHCR"
  if docker pull "$image_ref" >>"$LOG_FILE" 2>&1; then
    docker tag "$image_ref" "$local_tag"
    log "      $name: GHCR pull complete (cached layers reused)"
    return 0
  fi

  log "      $name: anonymous GHCR pull unavailable; using component Release fallback"
  ensure_zstd
  fallback_base="https://github.com/${REPO_SLUG}/releases/download/${fallback_tag}"
  fallback_sums="$(mktemp /tmp/otclick-component-sums.XXXXXX)"
  fallback_file="$(mktemp /tmp/otclick-component.XXXXXX.tar.zst)"
  curl -fsSL --retry 3 --retry-delay 2 "${fallback_base}/SHA256SUMS" -o "$fallback_sums" \
    || { rm -f "$fallback_sums" "$fallback_file"; return 21; }
  expected="$(awk -v asset="$fallback_asset" '$2 == asset || $2 == "./" asset {print $1; exit}' "$fallback_sums")"
  [[ -n "$expected" ]] || { rm -f "$fallback_sums" "$fallback_file"; return 22; }
  curl -fL --retry 3 --retry-delay 2 --retry-all-errors \
    "${fallback_base}/${fallback_asset}" -o "$fallback_file" >>"$LOG_FILE" 2>&1
  actual="$(sha256sum "$fallback_file" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || { rm -f "$fallback_sums" "$fallback_file"; return 23; }
  zstd -d -c "$fallback_file" | docker load >>"$LOG_FILE" 2>&1
  rm -f "$fallback_sums" "$fallback_file"
  docker image inspect "$local_tag" >/dev/null 2>&1 || return 24
  log "      $name: fallback image loaded"
}

log "[3/7] updating changed application components"
if [[ "$BACKEND_CHANGED" == "1" ]]; then
  pull_component backend "$TARGET_BACKEND_IMAGE" "$TARGET_BACKEND_FALLBACK_TAG" "$TARGET_BACKEND_FALLBACK_ASSET" "aiautoclicker-backend:latest"
else
  log "      backend exact image already present; 0 application bytes downloaded"
fi
if [[ "$FRONTEND_CHANGED" == "1" ]]; then
  pull_component frontend "$TARGET_FRONTEND_IMAGE" "$TARGET_FRONTEND_FALLBACK_TAG" "$TARGET_FRONTEND_FALLBACK_ASSET" "aiautoclicker-frontend:latest"
else
  log "      frontend exact image already present; 0 application bytes downloaded"
fi

if [[ "$MIGRATIONS_CHANGED" == "1" ]]; then
  log "[4/7] schema changed; backing up PostgreSQL"
  backup_database
else
  log "[4/7] schema unchanged; DB backup skipped"
fi

log "[5/7] fast-forwarding repository"
git checkout "$REF" >>"$LOG_FILE" 2>&1
git merge --ff-only "origin/$REF" >>"$LOG_FILE" 2>&1
[[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || die "repository did not reach target revision"
[[ -f docker-compose.prebuilt.yml ]] || die "docker-compose.prebuilt.yml is missing after update"
[[ -f infra/frontend-runtime-env.sh ]] || die "frontend runtime env injector is missing after update"

if [[ "$COMPOSE_CHANGED" == "1" ]]; then
  log "      compose changed; refreshing pinned third-party images"
  compose pull db migrate auth rest realtime storage storage-init kong >>"$LOG_FILE" 2>&1
else
  log "      compose unchanged; third-party image pull skipped"
fi

log "[6/7] reconciling/repairing stack without local builds"
# Always run the idempotent migration job and reconcile every runtime service.
# This is deliberate even when git is already current: a repeated installer run
# doubles as a safe repair after an interrupted deployment.
compose up -d --no-build --pull never db migrate >>"$LOG_FILE" 2>&1
wait_migrate
compose up -d --no-build --pull never db auth rest realtime storage storage-init kong >>"$LOG_FILE" 2>&1
require_http http://127.0.0.1:54321/auth/v1/health Supabase-auth auth 90

if [[ "$BACKEND_CHANGED" == "1" || "$COMPOSE_CHANGED" == "1" || "$INFRA_CHANGED" == "1" ]]; then
  compose up -d --no-build --pull never --force-recreate api worker >>"$LOG_FILE" 2>&1
else
  compose up -d --no-build --pull never api worker >>"$LOG_FILE" 2>&1
fi
if ! wait_http http://127.0.0.1:8000 backend 45; then
  log "      backend health failed; force-recreating api/worker once"
  runtime_diagnostics api
  compose up -d --no-build --pull never --force-recreate api worker >>"$LOG_FILE" 2>&1
  require_http http://127.0.0.1:8000 backend api 90
fi

if [[ "$FRONTEND_CHANGED" == "1" || "$COMPOSE_CHANGED" == "1" || "$INFRA_CHANGED" == "1" ]]; then
  compose up -d --no-build --pull never --force-recreate frontend >>"$LOG_FILE" 2>&1
else
  compose up -d --no-build --pull never frontend >>"$LOG_FILE" 2>&1
fi
if ! wait_http http://127.0.0.1:3000 frontend 45; then
  log "      frontend health failed; force-recreating frontend once"
  runtime_diagnostics frontend
  compose up -d --no-build --pull never --force-recreate frontend >>"$LOG_FILE" 2>&1
  require_http http://127.0.0.1:3000 frontend frontend 90
fi

python3 - "$STATE_DIR/install-state.json" "$TARGET_SHA" "$TARGET_BACKEND_HASH" "$TARGET_FRONTEND_HASH" "$TARGET_COMPOSE_HASH" "$TARGET_INFRA_HASH" "$TARGET_MIGRATIONS_HASH" <<'PY'
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

path = Path(sys.argv[1])
state = {
    "schema_version": 1,
    "git_sha": sys.argv[2],
    "backend_hash": sys.argv[3],
    "frontend_hash": sys.argv[4],
    "compose_hash": sys.argv[5],
    "infra_hash": sys.argv[6],
    "migrations_hash": sys.argv[7],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY

log "[7/7] update/repair complete"
log "      revision: $TARGET_SHA"
log "      application components pulled: backend=$BACKEND_CHANGED frontend=$FRONTEND_CHANGED"
if [[ -n "$BACKUP_FILE" ]]; then
  log "      DB backup: $BACKUP_FILE"
fi
log "      frontend: http://127.0.0.1:3000 healthy"
log "      backend: http://127.0.0.1:8000/health healthy"
log "      log: $LOG_FILE"
