#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${OTCLICK_REPO_URL:-https://github.com/gest0r1/Otclick-hh.git}"
BRANCH="${OTCLICK_BRANCH:-main}"
DEFAULT_TARGET_DIR="/opt/otclick-hh"

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
need docker
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose)."

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
  docker container inspect aiautoclicker-db >/dev/null 2>&1 \
    || docker volume inspect otclick-hh_supabase-db-data >/dev/null 2>&1
}

if [[ ! -f .env ]]; then
  if existing_database_detected; then
    die "Existing Otclick PostgreSQL data was detected, but $TARGET_DIR/.env is missing. Refusing to generate new secrets. Restore the original .env before continuing."
  fi

  log "Creating .env with generated secrets"
  python3 infra/bootstrap.py --openai-key ""
  printf '\nOPENAI_API_KEY is empty. Add your OpenAI-compatible key to %s/.env when needed.\n' "$TARGET_DIR"
else
  log "Keeping existing .env unchanged"
fi

log "Building and starting Docker Compose stack"
docker compose up -d --build

log "Stack status"
docker compose ps

printf '\nOtclick is installed/updated in: %s\n' "$TARGET_DIR"
printf 'Open: http://localhost:3000\n'
printf 'Run this same installer command again to update to the latest main.\n'