#!/usr/bin/env bash
set -Eeuo pipefail

REF="${OTCLICK_REF:-feature/persistent-vacancy-funnel}"
RAW_BASE="https://raw.githubusercontent.com/gest0r1/Otclick-hh/${REF}"
TMP_INSTALL="$(mktemp /tmp/otclick-install.XXXXXX.sh)"
trap 'rm -f "$TMP_INSTALL"' EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (use sudo)." >&2
  exit 1
fi

curl -fsSL "$RAW_BASE/install.sh" -o "$TMP_INSTALL"

# Feature-branch bootstrap patches only known installer blocks. Every replacement
# is exact: if install.sh changes underneath us, fail instead of applying a
# partial/unsafe patch. After these changes are folded into main this wrapper can
# become a trivial downloader again.
python3 - "$TMP_INSTALL" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if text.count(old) != 1:
        raise SystemExit(f"installer {label} block changed; refusing an unsafe blind patch")
    text = text.replace(old, new, 1)


replace_once(
'''install_packages() {
  log "installing host prerequisites"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y git curl ca-certificates python3 docker.io
  if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is unavailable"
}
''',
'''install_packages() {
  log "[1/8] checking host prerequisites"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update >>"$LOG_FILE" 2>&1
  apt-get install -y git curl ca-certificates python3 >>"$LOG_FILE" 2>&1

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "      existing Docker + Compose detected; package stack left untouched"
  elif command -v docker >/dev/null 2>&1; then
    if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'ok installed' || \
       dpkg-query -W -f='${Status}' containerd.io 2>/dev/null | grep -q 'ok installed'; then
      log "      Docker CE/containerd.io detected; installing matching Compose plugin"
      apt-get install -y docker-compose-plugin >>"$LOG_FILE" 2>&1
    else
      log "      Ubuntu docker.io detected; installing matching Compose v2 package"
      apt-get install -y docker-compose-v2 >>"$LOG_FILE" 2>&1
    fi
  elif dpkg-query -W -f='${Status}' containerd.io 2>/dev/null | grep -q 'ok installed' || \
       apt-cache policy docker-ce 2>/dev/null | grep -Eq 'Candidate: [^ (]'; then
    log "      Docker upstream repository/containerd.io detected; installing Docker CE stack"
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >>"$LOG_FILE" 2>&1
  else
    log "      installing Ubuntu Docker stack"
    apt-get install -y docker.io docker-compose-v2 >>"$LOG_FILE" 2>&1
  fi

  systemctl enable --now docker >>"$LOG_FILE" 2>&1
  docker version >/dev/null 2>&1 || die "Docker daemon is unavailable"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is unavailable"
}
''',
"prerequisite",
)

replace_once(
'''start_stack() {
  log "validating docker compose configuration"
  docker compose config >/dev/null
  log "building and starting stack"
  docker compose up -d --build
  wait_migrate
  wait_http http://127.0.0.1:8000/health backend 90
  wait_http http://127.0.0.1:3000 frontend 90
  wait_http http://127.0.0.1:54321/auth/v1/health Supabase-auth 90
}
''',
'''start_stack() {
  log "[5/8] validating Docker Compose configuration"
  docker compose config >/dev/null

  # Pull only third-party services. `worker` intentionally reuses the backend
  # image built by `api`; asking Compose to pull every service makes it try to
  # fetch the local-only aiautoclicker-backend:latest from Docker Hub.
  log "[6/8] pulling third-party Docker images (details -> $LOG_FILE)"
  docker compose pull db migrate auth rest realtime storage storage-init kong caddy >>"$LOG_FILE" 2>&1

  log "[7/8] building Otclick backend/frontend (details -> $LOG_FILE)"
  docker compose build api frontend >>"$LOG_FILE" 2>&1

  log "[8/8] starting services and running health checks"
  docker compose up -d --no-build --pull never >>"$LOG_FILE" 2>&1
  wait_migrate
  wait_http http://127.0.0.1:8000/health backend 90
  wait_http http://127.0.0.1:3000 frontend 90
  wait_http http://127.0.0.1:54321/auth/v1/health Supabase-auth 90
}
''',
"stack-start",
)

replace_once(
'''on_error() {
  local code=$?
  echo
  echo "[otclick] installation/update failed (exit $code)."
  if [[ -n "$PREVIOUS_SHA" ]]; then
    echo "Previous git revision: $PREVIOUS_SHA"
  fi
  if [[ -n "$BACKUP_FILE" ]]; then
    echo "Database backup: $BACKUP_FILE"
  fi
  echo "Log: $LOG_FILE"
  echo "No automatic DB rollback was attempted. Forward migrations may not be backward-compatible."
  exit "$code"
}
''',
'''on_error() {
  local code=$?
  echo
  echo "[otclick] installation/update failed (exit $code)."
  if [[ -n "$PREVIOUS_SHA" ]]; then
    echo "Previous git revision: $PREVIOUS_SHA"
  fi
  if [[ -n "$BACKUP_FILE" ]]; then
    echo "Database backup: $BACKUP_FILE"
  fi
  echo "Log: $LOG_FILE"
  echo "Last diagnostic lines:"
  tail -n 30 "$LOG_FILE" 2>/dev/null || true
  echo "No automatic DB rollback was attempted. Forward migrations may not be backward-compatible."
  exit "$code"
}
''',
"error-handler",
)

path.write_text(text, encoding="utf-8")
PY

chmod 700 "$TMP_INSTALL"
exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL"
