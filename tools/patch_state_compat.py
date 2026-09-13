from pathlib import Path

p = Path("install-update.sh")
s = p.read_text()
old = '''image_matches_target() {
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
'''
new = '''image_matches_target() {
  local target_hash="$1" target_ref="$2" local_tag="$3" component="$4" local_id target_id
  docker image inspect "$local_tag" >/dev/null 2>&1 || return 1
  local_id="$(docker image inspect "$local_tag" --format '{{.Id}}')"

  # Preferred v2 state: works for both Release-loaded and GHCR-pulled images.
  if [[ -f "$STATE_DIR/install-state.json" ]] && python3 - "$STATE_DIR/install-state.json" "$component" "$target_hash" "$local_id" <<'PYSTATE'
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
  then
    return 0
  fi

  # One-time compatibility with schema-v1 state: if an earlier GHCR-first
  # updater already pulled the exact digest, trust the content-addressed ref and
  # avoid redownloading the component merely to upgrade install-state metadata.
  docker image inspect "$target_ref" >/dev/null 2>&1 || return 1
  target_id="$(docker image inspect "$target_ref" --format '{{.Id}}')"
  [[ -n "$target_id" && "$target_id" == "$local_id" ]]
}
'''
assert old in s
s = s.replace(old, new, 1)
s = s.replace(
    'image_matches_target "$TARGET_BACKEND_HASH" aiautoclicker-backend:latest backend || BACKEND_CHANGED=1\nimage_matches_target "$TARGET_FRONTEND_HASH" aiautoclicker-frontend:latest frontend || FRONTEND_CHANGED=1',
    'image_matches_target "$TARGET_BACKEND_HASH" "$TARGET_BACKEND_IMAGE" aiautoclicker-backend:latest backend || BACKEND_CHANGED=1\nimage_matches_target "$TARGET_FRONTEND_HASH" "$TARGET_FRONTEND_IMAGE" aiautoclicker-frontend:latest frontend || FRONTEND_CHANGED=1',
    1,
)
p.write_text(s)
