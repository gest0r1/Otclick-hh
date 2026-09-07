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

# The original installer used Ubuntu's docker.io unconditionally. On hosts that
# already have Docker CE/containerd.io from download.docker.com this asks apt to
# install Ubuntu containerd as well, and apt correctly rejects the conflict.
# Patch only the host-prerequisite function before execution; the rest of the
# installer is byte-for-byte the repository version.
python3 - "$TMP_INSTALL" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '''install_packages() {
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
'''
new = '''install_packages() {
  log "installing host prerequisites"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y git curl ca-certificates python3

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "using existing Docker + Compose; package stack left untouched"
  elif command -v docker >/dev/null 2>&1; then
    if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'ok installed' || \
       dpkg-query -W -f='${Status}' containerd.io 2>/dev/null | grep -q 'ok installed'; then
      log "Docker CE/containerd.io detected; installing matching Compose plugin"
      apt-get install -y docker-compose-plugin
    else
      log "Ubuntu docker.io detected; installing matching Compose v2 package"
      apt-get install -y docker-compose-v2
    fi
  elif dpkg-query -W -f='${Status}' containerd.io 2>/dev/null | grep -q 'ok installed' || \
       apt-cache policy docker-ce 2>/dev/null | grep -Eq 'Candidate: [^ (]'; then
    log "Docker upstream repository/containerd.io detected; installing Docker CE stack"
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    log "installing Ubuntu Docker stack"
    apt-get install -y docker.io docker-compose-v2
  fi

  systemctl enable --now docker
  docker version >/dev/null 2>&1 || die "Docker daemon is unavailable"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is unavailable"
}
'''
if old not in text:
    raise SystemExit("installer prerequisite block changed; refusing an unsafe blind patch")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

chmod 700 "$TMP_INSTALL"
exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL"
