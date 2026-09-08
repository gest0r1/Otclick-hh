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
  apt-get install -y git curl ca-certificates python3 zstd >>"$LOG_FILE" 2>&1

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
'''ensure_candidate_files() {
  local source_dir="$INSTALL_DIR/backend/data/candidate"
  mkdir -p "$CANDIDATE_LOCAL_DIR"
  chmod 700 "$CANDIDATE_LOCAL_DIR"
  if [[ ! -f "$CANDIDATE_LOCAL_DIR/candidate_profile.json" ]]; then
    cp "$source_dir/candidate_profile.json" "$CANDIDATE_LOCAL_DIR/candidate_profile.json"
    chmod 600 "$CANDIDATE_LOCAL_DIR/candidate_profile.json"
    log "created local candidate profile override"
  fi
  if [[ ! -f "$CANDIDATE_LOCAL_DIR/confirmed_facts.json" ]]; then
    cp "$source_dir/confirmed_facts.json" "$CANDIDATE_LOCAL_DIR/confirmed_facts.json"
    chmod 600 "$CANDIDATE_LOCAL_DIR/confirmed_facts.json"
    log "created local confirmed-facts override"
  fi
}
''',
'''ensure_candidate_files() {
  local source_dir="$INSTALL_DIR/backend/data/candidate"
  mkdir -p "$CANDIDATE_LOCAL_DIR"
  if [[ ! -f "$CANDIDATE_LOCAL_DIR/candidate_profile.json" ]]; then
    cp "$source_dir/candidate_profile.json" "$CANDIDATE_LOCAL_DIR/candidate_profile.json"
    log "created local candidate profile override"
  fi
  if [[ ! -f "$CANDIDATE_LOCAL_DIR/confirmed_facts.json" ]]; then
    cp "$source_dir/confirmed_facts.json" "$CANDIDATE_LOCAL_DIR/confirmed_facts.json"
    log "created local confirmed-facts override"
  fi

  # backend/Dockerfile runs the API/worker as uid:gid 1000:1000. Keep the host
  # override private while ensuring that non-root container user can read the
  # read-only bind mount. Reapply on every install/update to repair older roots.
  chown 1000:1000 "$CANDIDATE_LOCAL_DIR" \
    "$CANDIDATE_LOCAL_DIR/candidate_profile.json" \
    "$CANDIDATE_LOCAL_DIR/confirmed_facts.json"
  chmod 700 "$CANDIDATE_LOCAL_DIR"
  chmod 600 "$CANDIDATE_LOCAL_DIR/candidate_profile.json" \
    "$CANDIDATE_LOCAL_DIR/confirmed_facts.json"
}
''',
"candidate-files",
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
  local git_sha release_tag release_base manifest_file sums_file bundle_file
  local manifest_sha expected_digest actual_digest
  git_sha="$(git rev-parse HEAD)"
  release_tag="install-${git_sha}"
  release_base="https://github.com/gest0r1/Otclick-hh/releases/download/${release_tag}"
  manifest_file="$(mktemp /tmp/otclick-manifest.XXXXXX.json)"
  sums_file="$(mktemp /tmp/otclick-sha256.XXXXXX.txt)"
  bundle_file="$(mktemp /tmp/otclick-images.XXXXXX.tar.zst)"

  # Release assets are the public distribution channel. GitHub Actions artifacts
  # remain the short-lived CI/diagnostic copy; the release tag is tied to the
  # exact repository commit so we never install images from another revision.
  if ! curl -fsSL --retry 2 --retry-delay 2 \
      "${release_base}/manifest.json" -o "$manifest_file"; then
    rm -f "$manifest_file" "$sums_file" "$bundle_file"
    echo "[otclick] prebuilt release is not ready for commit ${git_sha}." >&2
    echo "[otclick] Check: https://github.com/gest0r1/Otclick-hh/actions/workflows/build-artifact.yml" >&2
    echo "[otclick] Local build is intentionally disabled on low-memory hosts." >&2
    echo "[otclick] Emergency override: OTCLICK_ALLOW_LOCAL_BUILD=1" >&2
    return 22
  fi

  manifest_sha="$(python3 - "$manifest_file" <<'PYMAN'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(manifest.get("git_sha", ""))
PYMAN
  )"
  if [[ "$manifest_sha" != "$git_sha" ]]; then
    rm -f "$manifest_file" "$sums_file" "$bundle_file"
    echo "[otclick] release manifest SHA mismatch: ${manifest_sha} != ${git_sha}" >&2
    return 23
  fi

  curl -fsSL --retry 2 --retry-delay 2 \
    "${release_base}/SHA256SUMS" -o "$sums_file"
  expected_digest="$(awk '$2 == "otclick-images-linux-amd64.tar.zst" {print $1}' "$sums_file")"
  if [[ ! "$expected_digest" =~ ^[0-9a-f]{64}$ ]]; then
    rm -f "$manifest_file" "$sums_file" "$bundle_file"
    echo "[otclick] release SHA256SUMS does not contain the image bundle digest" >&2
    return 23
  fi

  log "[7/8] downloading prebuilt Otclick images (~1 GiB, no local build)"
  curl -fL --retry 3 --retry-delay 2 --retry-all-errors \
    "${release_base}/otclick-images-linux-amd64.tar.zst" \
    -o "$bundle_file" >>"$LOG_FILE" 2>&1

  actual_digest="$(sha256sum "$bundle_file" | awk '{print $1}')"
  if [[ "$actual_digest" != "$expected_digest" ]]; then
    rm -f "$manifest_file" "$sums_file" "$bundle_file"
    echo "[otclick] release bundle SHA-256 mismatch" >&2
    return 23
  fi

  log "      release bundle verified; loading Docker images"
  zstd -d -c "$bundle_file" | docker load >>"$LOG_FILE" 2>&1
  rm -f "$manifest_file" "$sums_file" "$bundle_file"

  docker image inspect aiautoclicker-backend:latest >/dev/null 2>&1 || return 24
  docker image inspect aiautoclicker-frontend:latest >/dev/null 2>&1 || return 24
}

diagnose_stack() {
  local service id state health exit_code
  echo >&2
  echo "[otclick] compose status:" >&2
  docker compose ps -a >&2 || true
  echo >&2
  echo "[otclick] logs for exited/unhealthy services:" >&2

  for service in db migrate auth rest realtime storage storage-init kong api worker frontend caddy; do
    id="$(docker compose ps -a -q "$service" 2>/dev/null || true)"
    [[ -n "$id" ]] || continue
    state="$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || true)"
    exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$id" 2>/dev/null || true)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null || true)"

    if [[ "$state" != "running" || "$health" == "unhealthy" ]]; then
      if [[ "$state" == "exited" && "$exit_code" == "0" && ( "$service" == "migrate" || "$service" == "storage-init" ) ]]; then
        continue
      fi
      echo >&2
      echo "[otclick] --- ${service}: state=${state:-unknown} exit=${exit_code:-?} health=${health:-n/a} ---" >&2
      docker compose logs --no-color --tail=120 "$service" >&2 || true
    fi
  done
}

compose_or_diagnose() {
  if ! docker compose "$@" >>"$LOG_FILE" 2>&1; then
    echo "[otclick] docker compose command failed: docker compose $*" >&2
    diagnose_stack
    return 1
  fi
}

start_stack() {
  log "[5/8] validating Docker Compose configuration"
  docker compose config >/dev/null

  log "[6/8] pulling third-party Docker images (details -> $LOG_FILE)"
  docker compose pull db migrate auth rest realtime storage storage-init kong caddy >>"$LOG_FILE" 2>&1

  if [[ "${OTCLICK_ALLOW_LOCAL_BUILD:-0}" == "1" ]]; then
    log "[7/8] emergency local build enabled (details -> $LOG_FILE)"
    docker compose build api frontend >>"$LOG_FILE" 2>&1
  else
    load_prebuilt_app_images
  fi

  log "[8/8] starting infrastructure and applying migrations"
  compose_or_diagnose up -d --no-build --pull never \
    db migrate auth rest realtime storage storage-init kong
  wait_migrate
  wait_http http://127.0.0.1:54321/auth/v1/health Supabase-auth 90

  # Docker Compose does not reliably recreate an existing container when a new
  # image is loaded under the same local `:latest` tag. Force only the app layer
  # so updates always run the exact images verified above without bouncing DB.
  log "      recreating api/frontend from current application images"
  compose_or_diagnose up -d --no-build --pull never --force-recreate --no-deps api frontend
  wait_http http://127.0.0.1:8000/health backend 90
  wait_http http://127.0.0.1:3000 frontend 90

  log "      recreating worker/caddy"
  compose_or_diagnose up -d --no-build --pull never --force-recreate --no-deps worker caddy
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
  echo "No automatic DB rollback was attempted. Forward migrations may not be backward-compatible."
  exit "$code"
}
''',
"error-handler",
)

replace_once(
'''  echo "After editing either local file, reload prepared candidate data with:"
  echo "  cd $INSTALL_DIR && docker compose up -d --build api worker && docker compose exec -T api python scripts/load_candidate_data.py --user-id '$user_id' --data-dir data/candidate-local"
''',
'''  echo "After editing either local file, reload prepared candidate data with:"
  echo "  cd $INSTALL_DIR && docker compose exec -T api python scripts/load_candidate_data.py --user-id '$user_id' --data-dir data/candidate-local"
''',
"candidate-reload",
)

path.write_text(text, encoding="utf-8")
PY

chmod 700 "$TMP_INSTALL"
exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL"
