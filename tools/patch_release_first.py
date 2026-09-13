from pathlib import Path
import re

p = Path('install-update.sh')
s = p.read_text()
s = s.replace(
    '# It downloads only a tiny exact-SHA manifest, then pulls changed application\n# components from GHCR so Docker can reuse cached layers. GitHub Release\n# component archives are a fallback only; the combined fresh-install bundle is\n# never downloaded by this script.',
    '# It downloads only a tiny exact-SHA manifest, then downloads only changed\n# application components. GitHub Release component archives are the production\n# default because some hosting networks route GHCR blob traffic very slowly; GHCR\n# remains an immutable fallback/override. The combined fresh-install bundle is\n# never downloaded by this script.'
)
s = s.replace(
    'log "      installing zstd for Release fallback"',
    'log "      installing zstd for component Release transport"'
)
s = s.replace(
    'command -v zstd >/dev/null 2>&1 || die "zstd is required only for the GitHub Release fallback, but could not be installed"',
    'command -v zstd >/dev/null 2>&1 || die "zstd is required for the GitHub Release component transport, but could not be installed"'
)

old = re.search(r'image_matches_target\(\) \{.*?\n\}\n\n\nimage_transfer_size\(\)', s, re.S)
assert old, 'image_matches_target block not found'
new = r'''image_matches_target() {
  local target_hash="$1" local_tag="$2" component="$3" local_id
  docker image inspect "$local_tag" >/dev/null 2>&1 || return 1
  [[ -f "$STATE_DIR/install-state.json" ]] || return 1
  local_id="$(docker image inspect "$local_tag" --format '{{.Id}}')"
  python3 - "$STATE_DIR/install-state.json" "$component" "$target_hash" "$local_id" <<'PYSTATE'
import json
import sys

path, component, target_hash, local_id = sys.argv[1:]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if data.get(f"{component}_hash") != target_hash:
    raise SystemExit(1)
if data.get(f"{component}_image_id") != local_id:
    raise SystemExit(1)
PYSTATE
}


image_transfer_size()'''
s = s[:old.start()] + new + s[old.end():]

s = s.replace(
    'image_matches_target "$TARGET_BACKEND_IMAGE" aiautoclicker-backend:latest || BACKEND_CHANGED=1\nimage_matches_target "$TARGET_FRONTEND_IMAGE" aiautoclicker-frontend:latest || FRONTEND_CHANGED=1',
    'image_matches_target "$TARGET_BACKEND_HASH" aiautoclicker-backend:latest backend || BACKEND_CHANGED=1\nimage_matches_target "$TARGET_FRONTEND_HASH" aiautoclicker-frontend:latest frontend || FRONTEND_CHANGED=1'
)

block = re.search(r'pull_component\(\) \{.*?\n\}\n\nlog "\[3/7\] updating changed application components"', s, re.S)
assert block, 'pull_component block not found'
replacement = r'''pull_component_from_release() {
  local name="$1" fallback_tag="$2" fallback_asset="$3" local_tag="$4"
  local fallback_base fallback_sums fallback_file expected actual

  ensure_zstd
  fallback_base="https://github.com/${REPO_SLUG}/releases/download/${fallback_tag}"
  fallback_sums="$(mktemp /tmp/otclick-component-sums.XXXXXX)"
  fallback_file="$(mktemp /tmp/otclick-component.XXXXXX.tar.zst)"
  curl -fsSL --retry 3 --retry-delay 2 "${fallback_base}/SHA256SUMS" -o "$fallback_sums" \
    || { rm -f "$fallback_sums" "$fallback_file"; return 21; }
  expected="$(awk -v asset="$fallback_asset" '$2 == asset || $2 == "./" asset {print $1; exit}' "$fallback_sums")"
  [[ -n "$expected" ]] || { rm -f "$fallback_sums" "$fallback_file"; return 22; }
  curl_download_visible "$name component Release" \
    "${fallback_base}/${fallback_asset}" "$fallback_file" || { rm -f "$fallback_sums" "$fallback_file"; return 23; }
  actual="$(sha256sum "$fallback_file" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || { rm -f "$fallback_sums" "$fallback_file"; return 24; }
  zstd -d -c "$fallback_file" | docker load >>"$LOG_FILE" 2>&1 || { rm -f "$fallback_sums" "$fallback_file"; return 25; }
  rm -f "$fallback_sums" "$fallback_file"
  docker image inspect "$local_tag" >/dev/null 2>&1 || return 26
  log "      $name: component Release loaded"
}

pull_component_from_ghcr() {
  local name="$1" image_ref="$2" local_tag="$3" transfer_size
  transfer_size="$(image_transfer_size "$image_ref" || true)"
  if [[ -n "$transfer_size" ]]; then
    log "      $name: pulling immutable image from GHCR (compressed image up to $transfer_size; cached layers are reused)"
  else
    log "      $name: pulling immutable image from GHCR"
  fi
  docker_pull_visible "$name" "$image_ref" || return $?
  docker tag "$image_ref" "$local_tag"
  log "      $name: GHCR pull complete (cached layers reused)"
}

pull_component() {
  local name="$1" image_ref="$2" fallback_tag="$3" fallback_asset="$4" local_tag="$5"
  local requested transport
  requested="${OTCLICK_IMAGE_TRANSPORT:-$(env_get OTCLICK_IMAGE_TRANSPORT)}"
  requested="${requested:-release}"
  case "$requested" in
    auto)
      # Release-first is intentionally the current auto policy. It preserves the
      # fast GitHub Release CDN path observed on production hosting while GHCR
      # remains a content-addressed fallback.
      transport="release"
      ;;
    release|ghcr)
      transport="$requested"
      ;;
    *)
      die "invalid OTCLICK_IMAGE_TRANSPORT=$requested (expected auto/release/ghcr)"
      ;;
  esac

  log "      $name: image transport=$requested (effective=$transport)"
  if [[ "$transport" == "release" ]]; then
    if pull_component_from_release "$name" "$fallback_tag" "$fallback_asset" "$local_tag"; then
      return 0
    fi
    log "      $name: component Release unavailable; falling back to GHCR"
    pull_component_from_ghcr "$name" "$image_ref" "$local_tag"
    return $?
  fi

  if pull_component_from_ghcr "$name" "$image_ref" "$local_tag"; then
    return 0
  fi
  log "      $name: GHCR unavailable; falling back to component Release"
  pull_component_from_release "$name" "$fallback_tag" "$fallback_asset" "$local_tag"
}

log "[3/7] updating changed application components"'''
s = s[:block.start()] + replacement + s[block.end():]

old_state = '''python3 - "$STATE_DIR/install-state.json" "$TARGET_SHA" "$TARGET_BACKEND_HASH" "$TARGET_FRONTEND_HASH" "$TARGET_COMPOSE_HASH" "$TARGET_INFRA_HASH" "$TARGET_MIGRATIONS_HASH" <<'PY'
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
'''
new_state = '''BACKEND_IMAGE_ID="$(docker image inspect aiautoclicker-backend:latest --format '{{.Id}}')"
FRONTEND_IMAGE_ID="$(docker image inspect aiautoclicker-frontend:latest --format '{{.Id}}')"
python3 - "$STATE_DIR/install-state.json" "$TARGET_SHA" "$TARGET_BACKEND_HASH" "$TARGET_FRONTEND_HASH" "$TARGET_COMPOSE_HASH" "$TARGET_INFRA_HASH" "$TARGET_MIGRATIONS_HASH" "$BACKEND_IMAGE_ID" "$FRONTEND_IMAGE_ID" <<'PY'
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

path = Path(sys.argv[1])
state = {
    "schema_version": 2,
    "git_sha": sys.argv[2],
    "backend_hash": sys.argv[3],
    "frontend_hash": sys.argv[4],
    "compose_hash": sys.argv[5],
    "infra_hash": sys.argv[6],
    "migrations_hash": sys.argv[7],
    "backend_image_id": sys.argv[8],
    "frontend_image_id": sys.argv[9],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
'''
assert old_state in s, 'state block not found'
s = s.replace(old_state, new_state)
p.write_text(s)

env = Path('.env.example')
e = env.read_text()
marker = '# ─── Backend app ───\n'
insert = '''# Incremental application image transport. Release is the production default:
# it downloads only the changed backend/frontend .tar.zst component over the
# GitHub Release CDN, then verifies SHA-256 and docker-loads it. GHCR stays as
# the immutable fallback. Set ghcr to force Docker registry layer pulls.
OTCLICK_IMAGE_TRANSPORT=release

'''
assert marker in e
e = e.replace(marker, insert + marker, 1)
env.write_text(e)

t = Path('backend/tests/test_incremental_installer_contract.py')
x = t.read_text()
x = re.sub(
    r'def test_incremental_updater_uses_release_fallback_only_after_ghcr_failure\(\):\n(?:    .*\n)+?\n(?=def )',
    '''def test_incremental_updater_is_release_first_with_configurable_transport():
    updater = _read("install-update.sh")
    env_example = _read(".env.example")
    assert 'OTCLICK_IMAGE_TRANSPORT:-$(env_get OTCLICK_IMAGE_TRANSPORT)' in updater
    assert 'requested="${requested:-release}"' in updater
    assert 'auto)' in updater
    assert 'release|ghcr)' in updater
    assert 'component Release unavailable; falling back to GHCR' in updater
    assert 'GHCR unavailable; falling back to component Release' in updater
    assert 'OTCLICK_IMAGE_TRANSPORT=release' in env_example


''',
    x,
)
x = re.sub(
    r'def test_incremental_updater_requires_exact_target_digest_not_just_latest_tag\(\):\n(?:    .*\n)+?\n(?=def )',
    '''def test_incremental_updater_verifies_loaded_image_against_persisted_component_state():
    updater = _read("install-update.sh")
    assert 'image_matches_target()' in updater
    assert 'install-state.json' in updater
    assert 'backend_image_id' in updater
    assert 'frontend_image_id' in updater
    assert 'image_matches_target "$TARGET_BACKEND_HASH" aiautoclicker-backend:latest backend || BACKEND_CHANGED=1' in updater
    assert 'image_matches_target "$TARGET_FRONTEND_HASH" aiautoclicker-frontend:latest frontend || FRONTEND_CHANGED=1' in updater


''',
    x,
)
t.write_text(x)

tb = Path('backend/tests/test_build_artifact_contract.py')
b = tb.read_text()
if 'test_component_builds_use_persistent_buildx_cache' not in b:
    b += '''


def test_component_builds_use_persistent_buildx_cache():
    workflow = _workflow()
    assert "docker/setup-buildx-action@" in workflow
    assert "docker/build-push-action@" in workflow
    assert "cache-from: type=gha,scope=backend" in workflow
    assert "cache-to: type=gha,mode=max,scope=backend" in workflow
    assert "cache-from: type=gha,scope=frontend" in workflow
    assert "cache-to: type=gha,mode=max,scope=frontend" in workflow
'''
tb.write_text(b)
