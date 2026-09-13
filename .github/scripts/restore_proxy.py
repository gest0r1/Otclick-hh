from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


CADDYFILE = """{$CADDY_SITE_ADDRESS} {
  encode zstd gzip

  # FastAPI lives under /api/*; expose /health for installer/runtime checks.
  @backend path /api/* /health
  handle @backend {
    reverse_proxy api:8000
  }

  # Supabase browser endpoints share the same public origin. Caddy preserves
  # websocket upgrades for Realtime automatically.
  @supabase path /auth/* /rest/* /realtime/* /storage/*
  handle @supabase {
    reverse_proxy kong:8000
  }

  # Everything else is the Next.js application.
  handle {
    reverse_proxy frontend:3000
  }
}
"""
Path("infra/Caddyfile").write_text(CADDYFILE, encoding="utf-8")

# Restore loopback-only service ports and the Caddy routing service.
path = "docker-compose.yml"
text = read(path)
text = replace_once(text, '      - "54321:8000"', '      - "127.0.0.1:54321:8000"', "kong loopback bind")
text = replace_once(text, '      - "8000:8000"', '      - "127.0.0.1:8000:8000"', "api loopback bind")
text = replace_once(text, '      - "3000:3000"', '      - "127.0.0.1:3000:3000"', "frontend loopback bind")
old_tail = """    depends_on:
      - api

volumes:
  supabase-db-data:
  supabase-storage-data:
"""
new_tail = """    depends_on:
      - api

  caddy:
    image: caddy:2-alpine
    container_name: aiautoclicker-caddy
    restart: unless-stopped
    depends_on:
      api:
        condition: service_healthy
      kong:
        condition: service_healthy
      frontend:
        condition: service_started
    environment:
      CADDY_SITE_ADDRESS: "${CADDY_SITE_ADDRESS:-:80}"
    ports:
      # Direct mode uses host 80/443. When another reverse proxy already owns
      # those ports, the installer switches Caddy to loopback 18080/18443.
      - "${CADDY_HTTP_BIND:-80}:80"
      - "${CADDY_HTTPS_BIND:-443}:443"
      - "${CADDY_HTTPS_BIND:-443}:443/udp"
    volumes:
      - ./infra/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config

volumes:
  supabase-db-data:
  supabase-storage-data:
  caddy-data:
  caddy-config:
"""
text = replace_once(text, old_tail, new_tail, "caddy service")
write(path, text)

# Proxy-mode variables for fresh installs. Existing .env remains intact except
# for these non-secret listener settings when the installer reconciles them.
path = ".env.example"
text = read(path)
marker = "SERVICE_ROLE_KEY=\n\n"
proxy_env = """SERVICE_ROLE_KEY=

# ─── Reverse proxy / production listener ───
# auto: use direct 80/443 unless another public reverse proxy already owns them.
# external: bind Caddy only to loopback 18080/18443; an existing proxy can reach
# the Caddy container over the shared Docker network or use the loopback upstream.
OTCLICK_PROXY_MODE=auto
CADDY_SITE_ADDRESS=:80
CADDY_HTTP_BIND=80
CADDY_HTTPS_BIND=443

"""
text = replace_once(text, marker, proxy_env, "proxy env block")
write(path, text)

PROXY_HELPERS = r'''
env_get() {
  local key="$1"
  [[ -f .env ]] || return 0
  sed -n "s/^${key}=//p" .env | tail -n 1
}

env_set() {
  local key="$1" value="$2"
  python3 - .env "$key" "$value" <<'PYENV'
from pathlib import Path
import sys
path = Path(sys.argv[1])
key, value = sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
prefix = key + "="
out = []
replaced = False
for line in lines:
    if line.startswith(prefix):
        if not replaced:
            out.append(prefix + value)
            replaced = True
        continue
    out.append(line)
if not replaced:
    out.append(prefix + value)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PYENV
  chmod 600 .env
}

foreign_public_proxy() {
  docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
    | grep -Ev '^aiautoclicker-caddy[[:space:]]' \
    | grep -Eq '(^|,|[[:space:]])(0\.0\.0\.0:|\[::\]:)?(80|443)->'
}

configure_proxy_mode() {
  local explicit_mode mode
  explicit_mode="${OTCLICK_PROXY_MODE:-}"
  mode="${explicit_mode:-$(env_get OTCLICK_PROXY_MODE)}"

  if [[ -z "$mode" || "$mode" == "auto" ]]; then
    if foreign_public_proxy; then
      mode="external"
    else
      mode="direct"
    fi
  fi

  case "$mode" in
    direct)
      env_set OTCLICK_PROXY_MODE direct
      env_set CADDY_HTTP_BIND "80"
      env_set CADDY_HTTPS_BIND "443"
      log "      proxy mode: direct Caddy on host 80/443"
      ;;
    external)
      env_set OTCLICK_PROXY_MODE external
      env_set CADDY_HTTP_BIND "127.0.0.1:${OTCLICK_INTERNAL_HTTP_PORT:-18080}"
      env_set CADDY_HTTPS_BIND "127.0.0.1:${OTCLICK_INTERNAL_HTTPS_PORT:-18443}"
      env_set CADDY_SITE_ADDRESS ":80"
      log "      proxy mode: external reverse proxy; Caddy on loopback ${OTCLICK_INTERNAL_HTTP_PORT:-18080}/${OTCLICK_INTERNAL_HTTPS_PORT:-18443}"
      ;;
    *)
      die "invalid OTCLICK_PROXY_MODE=$mode (expected auto/direct/external)"
      ;;
  esac
}

caddy_health_url() {
  local bind port
  bind="$(env_get CADDY_HTTP_BIND)"
  bind="${bind:-80}"
  port="${bind##*:}"
  printf 'http://127.0.0.1:%s/health' "$port"
}
'''

# Fresh installer: keep current GHCR/prebuilt design and add Caddy lifecycle.
path = "install.sh"
text = read(path)
compose_block = '''compose() {
  docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml "$@"
}
'''
text = replace_once(text, compose_block, compose_block + PROXY_HELPERS, "fresh installer proxy helpers")
text = replace_once(
    text,
    'load_prebuilt_images\n\nlog "Pulling third-party infrastructure images"',
    'configure_proxy_mode\n\nload_prebuilt_images\n\nlog "Pulling third-party infrastructure images"',
    "fresh configure proxy",
)
text = replace_once(text, 'compose pull db migrate auth rest realtime storage storage-init kong', 'compose pull db migrate auth rest realtime storage storage-init kong caddy', "fresh caddy pull")
text = replace_once(
    text,
    'compose up -d --no-build --pull never --force-recreate api frontend worker\n\nlog "Stack status"',
    'compose up -d --no-build --pull never --force-recreate api frontend worker\n\nlog "Starting Caddy routing layer"\ncompose up -d --no-build --pull never --force-recreate --no-deps caddy\n\nlog "Stack status"',
    "fresh caddy start",
)
write(path, text)

# Incremental updater: repair known stale empty path, reconcile Caddy and verify it.
path = "install-update.sh"
text = read(path)
text = replace_once(text, compose_block, compose_block + PROXY_HELPERS, "updater proxy helpers")
merge_marker = '''log "[5/7] fast-forwarding repository"
git checkout "$REF" >>"$LOG_FILE" 2>&1
'''
merge_replacement = '''log "[5/7] fast-forwarding repository"
# A broken previous deployment can leave infra/Caddyfile as an empty directory
# after the tracked file disappeared from main. Remove only that exact empty
# directory so Git can restore the tracked Caddyfile; never delete its contents.
if [[ -d infra/Caddyfile ]]; then
  if rmdir infra/Caddyfile 2>/dev/null; then
    log "      repaired stale empty infra/Caddyfile directory"
  else
    die "infra/Caddyfile is a non-empty directory; move it aside manually before updating"
  fi
fi
git checkout "$REF" >>"$LOG_FILE" 2>&1
'''
text = replace_once(text, merge_marker, merge_replacement, "stale Caddyfile repair")
post_merge = '''[[ -f docker-compose.prebuilt.yml ]] || die "docker-compose.prebuilt.yml is missing after update"
[[ -f infra/frontend-runtime-env.sh ]] || die "frontend runtime env injector is missing after update"
'''
text = replace_once(text, post_merge, post_merge + '[[ -f infra/Caddyfile ]] || die "infra/Caddyfile is missing after update"\nconfigure_proxy_mode\n', "updater configure proxy")
text = replace_once(text, 'compose pull db migrate auth rest realtime storage storage-init kong >>"$LOG_FILE" 2>&1', 'compose pull db migrate auth rest realtime storage storage-init kong caddy >>"$LOG_FILE" 2>&1', "updater caddy pull")
frontend_tail = '''  require_http http://127.0.0.1:3000 frontend frontend 90
fi

python3 - "$STATE_DIR/install-state.json"'''
caddy_tail = '''  require_http http://127.0.0.1:3000 frontend frontend 90
fi

CADDY_HEALTH_URL="$(caddy_health_url)"
if [[ "$COMPOSE_CHANGED" == "1" || "$INFRA_CHANGED" == "1" ]]; then
  compose up -d --no-build --pull never --force-recreate --no-deps caddy >>"$LOG_FILE" 2>&1
else
  compose up -d --no-build --pull never --no-deps caddy >>"$LOG_FILE" 2>&1
fi
require_http "$CADDY_HEALTH_URL" internal-Caddy caddy 60

python3 - "$STATE_DIR/install-state.json"'''
text = replace_once(text, frontend_tail, caddy_tail, "updater caddy reconcile")
text = replace_once(
    text,
    'log "      backend: http://127.0.0.1:8000/health healthy"\nlog "      log: $LOG_FILE"',
    'log "      backend: http://127.0.0.1:8000/health healthy"\nlog "      caddy: $CADDY_HEALTH_URL healthy"\nlog "      log: $LOG_FILE"',
    "updater caddy success log",
)
write(path, text)

# Contract tests.
path = "backend/tests/test_incremental_installer_contract.py"
text = read(path)
addition = r'''


def test_production_proxy_contract_uses_loopback_service_ports_and_caddy():
    compose = _read("docker-compose.yml")
    caddy = _read("infra/Caddyfile")
    env_example = _read(".env.example")

    assert '"127.0.0.1:3000:3000"' in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"127.0.0.1:54321:8000"' in compose
    assert 'container_name: aiautoclicker-caddy' in compose
    assert './infra/Caddyfile:/etc/caddy/Caddyfile:ro' in compose
    assert 'reverse_proxy frontend:3000' in caddy
    assert 'reverse_proxy api:8000' in caddy
    assert 'reverse_proxy kong:8000' in caddy
    assert 'OTCLICK_PROXY_MODE=auto' in env_example
    assert 'CADDY_HTTP_BIND=80' in env_example
    assert 'CADDY_HTTPS_BIND=443' in env_example


def test_installers_restore_and_reconcile_caddy_without_local_app_builds():
    fresh = _read("install.sh")
    updater = _read("install-update.sh")

    for script in (fresh, updater):
        assert 'configure_proxy_mode()' in script
        assert 'foreign_public_proxy()' in script
        assert 'caddy_health_url()' in script

    assert 'compose pull db migrate auth rest realtime storage storage-init kong caddy' in fresh
    assert '--force-recreate --no-deps caddy' in fresh
    assert 'repaired stale empty infra/Caddyfile directory' in updater
    assert 'compose pull db migrate auth rest realtime storage storage-init kong caddy' in updater
    assert 'require_http "$CADDY_HEALTH_URL" internal-Caddy caddy 60' in updater
    assert 'docker compose build' not in updater
'''
if "test_production_proxy_contract_uses_loopback_service_ports_and_caddy" not in text:
    text += addition
write(path, text)
