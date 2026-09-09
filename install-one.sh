#!/usr/bin/env bash
set -Eeuo pipefail

# Backward-compatible shim. The canonical installer is now install.sh.
REF="${OTCLICK_REF:-feature/persistent-vacancy-funnel}"
RAW_URL="https://raw.githubusercontent.com/gest0r1/Otclick-hh/${REF}/install.sh"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (use sudo)." >&2
  exit 1
fi

TMP_INSTALL="$(mktemp /tmp/otclick-install.XXXXXX.sh)"
trap 'rm -f "$TMP_INSTALL"' EXIT
curl -fsSL "$RAW_URL" -o "$TMP_INSTALL"
chmod 700 "$TMP_INSTALL"
exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL" "$@"
