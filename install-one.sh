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
  apt-get install -y git curl ca-certificates python3 unzip zstd >>"$LOG_FILE" 2>&1

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
'''load_prebuilt_app_images() {
  local git_sha artifact_name api_url metadata_file artifact_url artifact_digest artifact_size
  local artifact_zip actual_digest bundle_member
  git_sha="$(git rev-parse HEAD)"
  artifact_name="otclick-linux-amd64-${git_sha}"
  api_url="https://api.github.com/repos/gest0r1/Otclick-hh/actions/artifacts?name=${artifact_name}&per_page=100"
  metadata_file="$(mktemp /tmp/otclick-artifact-meta.XXXXXX.json)"
  artifact_zip="$(mktemp /tmp/otclick-artifact.XXXXXX.zip)"

  # GitHub documents unauthenticated read access for public repository Actions
  # artifacts. Select only an unexpired artifact produced from this exact SHA.
  curl -fsSL \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "$api_url" -o "$metadata_file"

  mapfile -t artifact_meta < <(python3 - "$metadata_file" "$git_sha" "$artifact_name" <<'PYART'
import json
import sys

path, sha, name = sys.argv[1:]
data = json.load(open(path, encoding="utf-8"))
for artifact in data.get("artifacts", []):
    if artifact.get("name") != name or artifact.get("expired"):
        continue
    run = artifact.get("workflow_run") or {}
    if run.get("head_sha") != sha:
        continue
    print(artifact.get("archive_download_url", ""))
    print(artifact.get("digest", ""))
    print(artifact.get("size_in_bytes", 0))
    break
PYART
  )
  rm -f "$metadata_file"

  if [[ "${#artifact_meta[@]}" -lt 3 || -z "${artifact_meta[0]}" ]]; then
    echo "[otclick] prebuilt artifact is not ready for commit ${git_sha}." >&2
    echo "[otclick] Check: https://github.com/gest0r1/Otclick-hh/actions/workflows/build-artifact.yml" >&2
    echo "[otclick] Local build is intentionally disabled on low-memory hosts." >&2
    echo "[otclick] Emergency override: OTCLICK_ALLOW_LOCAL_BUILD=1" >&2
    return 22
  fi

  artifact_url="${artifact_meta[0]}"
  artifact_digest="${artifact_meta[1]}"
  artifact_size="${artifact_meta[2]}"
  log "[7/8] downloading prebuilt Otclick images (~$((artifact_size / 1024 / 1024)) MiB)"

  curl -fL --retry 3 --retry-delay 2 --retry-all-errors \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "$artifact_url" -o "$artifact_zip" >>"$LOG_FILE" 2>&1

  if [[ "$artifact_digest" == sha256:* ]]; then
    actual_digest="sha256:$(sha256sum "$artifact_zip" | awk '{print $1}')"
    if [[ "$actual_digest" != "$artifact_digest" ]]; then
      rm -f "$artifact_zip"
      echo "[otclick] artifact SHA-256 mismatch: expected $artifact_digest, got $actual_digest" >&2
      return 23
    fi
  fi

  bundle_member="$(python3 - "$artifact_zip" "$git_sha" <<'PYZIP'
import json
import sys
import zipfile

archive, expected_sha = sys.argv[1:]
with zipfile.ZipFile(archive) as zf:
    names = zf.namelist()
    manifest_name = next((n for n in names if n == "manifest.json" or n.endswith("/manifest.json")), None)
    if not manifest_name:
        raise SystemExit("manifest.json missing from artifact")
    manifest = json.loads(zf.read(manifest_name))
    if manifest.get("git_sha") != expected_sha:
        raise SystemExit(
            f"artifact manifest SHA mismatch: {manifest.get('git_sha')} != {expected_sha}"
        )
    bundle = manifest.get("bundle") or "otclick-images-linux-amd64.tar.zst"
    member = next((n for n in names if n == bundle or n.endswith("/" + bundle)), None)
    if not member:
        raise SystemExit(f"{bundle} missing from artifact")
    print(member)
PYZIP
  )"

  log "      GitHub artifact verified; loading Docker images"
  unzip -p "$artifact_zip" "$bundle_member" | zstd -d -c | docker load >>"$LOG_FILE" 2>&1
  rm -f "$artifact_zip"

  docker image inspect aiautoclicker-backend:latest >/dev/null 2>&1 || return 24
  docker image inspect aiautoclicker-frontend:latest >/dev/null 2>&1 || return 24
}

start_stack() {
  log "[5/8] validating Docker Compose configuration"
  docker compose config >/dev/null

  # Pull only third-party services. `worker` intentionally reuses the backend
  # image loaded below; never ask Compose to pull local app images from Docker Hub.
  log "[6/8] pulling third-party Docker images (details -> $LOG_FILE)"
  docker compose pull db migrate auth rest realtime storage storage-init kong caddy >>"$LOG_FILE" 2>&1

  if [[ "${OTCLICK_ALLOW_LOCAL_BUILD:-0}" == "1" ]]; then
    log "[7/8] emergency local build enabled (details -> $LOG_FILE)"
    docker compose build api frontend >>"$LOG_FILE" 2>&1
  else
    load_prebuilt_app_images
  fi

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
  trap - ERR
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
