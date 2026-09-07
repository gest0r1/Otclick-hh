#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${OTCLICK_REPO_URL:-https://github.com/gest0r1/Otclick-hh.git}"
REF="${OTCLICK_REF:-main}"
INSTALL_DIR="${OTCLICK_DIR:-/opt/otclick-hh}"
LOG_DIR="${OTCLICK_LOG_DIR:-/var/log/otclick-hh}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/install-$STAMP.log"
PREVIOUS_SHA=""
BACKUP_FILE=""
GENERATED_PASSWORD=""
INSTALL_ADMIN_PASSWORD=""
FRESH_ENV=0
LLM_VERIFY_STATUS="not configured"
PROFILE_STATUS="not checked"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"
chmod 700 "$LOG_DIR"
chmod 600 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[otclick] %s\n' "$*"; }
die() { printf '[otclick] ERROR: %s\n' "$*" >&2; exit 1; }

on_error() {
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
trap on_error ERR

have_tty() { [[ -r /dev/tty && -w /dev/tty ]]; }

prompt_text() {
  local label="$1" default_value="${2:-}" answer
  if ! have_tty; then
    printf '%s' "$default_value"
    return 0
  fi
  if [[ -n "$default_value" ]]; then
    printf '%s [%s]: ' "$label" "$default_value" >/dev/tty
  else
    printf '%s: ' "$label" >/dev/tty
  fi
  IFS= read -r answer </dev/tty
  printf '%s' "${answer:-$default_value}"
}

prompt_secret() {
  local label="$1" answer
  if ! have_tty; then
    printf ''
    return 0
  fi
  printf '%s: ' "$label" >/dev/tty
  IFS= read -r -s answer </dev/tty
  printf '\n' >/dev/tty
  printf '%s' "$answer"
}

prompt_yes_no() {
  local label="$1" default="${2:-n}" answer
  if ! have_tty; then
    [[ "$default" == "y" ]]
    return
  fi
  if [[ "$default" == "y" ]]; then
    printf '%s [Y/n]: ' "$label" >/dev/tty
  else
    printf '%s [y/N]: ' "$label" >/dev/tty
  fi
  IFS= read -r answer </dev/tty
  answer="${answer,,}"
  [[ -z "$answer" ]] && answer="$default"
  [[ "$answer" == "y" || "$answer" == "yes" || "$answer" == "д" || "$answer" == "да" ]]
}

require_ubuntu() {
  [[ -r /etc/os-release ]] || die "/etc/os-release not found"
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "Ubuntu is required; detected ${ID:-unknown}"
  case "${VERSION_ID:-}" in
    24.04|24.04.*) ;;
    *) log "warning: designed for Ubuntu 24.04; detected ${VERSION_ID:-unknown}" ;;
  esac
}

install_packages() {
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

env_get() {
  local key="$1"
  [[ -f .env ]] || return 0
  sed -n "s/^${key}=//p" .env | tail -n 1
}

env_set() {
  local key="$1" value="$2"
  python3 - .env "$key" "$value" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
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
PY
  chmod 600 .env
}

service_running() {
  local service="$1" id
  id="$(docker compose ps -q "$service" 2>/dev/null || true)"
  [[ -n "$id" ]] || return 1
  [[ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null || true)" == "true" ]]
}

backup_existing() {
  [[ -f docker-compose.yml && -f .env ]] || return 0
  if ! service_running db; then
    log "starting existing DB service for backup"
    docker compose up -d db
    local db_id db_health i
    for ((i=1; i<=60; i++)); do
      db_id="$(docker compose ps -q db 2>/dev/null || true)"
      db_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$db_id" 2>/dev/null || true)"
      [[ "$db_health" == "healthy" || "$db_health" == "running" ]] && break
      sleep 2
    done
    service_running db || die "could not start existing DB for backup"
  fi
  mkdir -p backups
  chmod 700 backups
  local backup_tmp backup_final
  backup_final="$INSTALL_DIR/backups/postgres-$STAMP.dump"
  backup_tmp="$backup_final.tmp"
  log "creating PostgreSQL backup: $backup_final"
  rm -f "$backup_tmp"
  docker compose exec -T db pg_dump -U postgres -d postgres -Fc > "$backup_tmp"
  chmod 600 "$backup_tmp"
  mv "$backup_tmp" "$backup_final"
  BACKUP_FILE="$backup_final"
  cp .env "$INSTALL_DIR/backups/env-$STAMP"
  chmod 600 "$INSTALL_DIR/backups/env-$STAMP"
}

checkout_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    cd "$INSTALL_DIR"
    PREVIOUS_SHA="$(git rev-parse HEAD)"
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
      die "tracked files in $INSTALL_DIR have local changes; commit/stash them before update"
    fi
    backup_existing
    log "updating repository to $REF"
    git fetch --prune origin
    if git show-ref --verify --quiet "refs/heads/$REF"; then
      git checkout "$REF"
    else
      git checkout -b "$REF" --track "origin/$REF"
    fi
    git merge --ff-only "origin/$REF"
  else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    log "cloning $REPO_URL ($REF) to $INSTALL_DIR"
    git clone --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
  fi
}

normalize_public_url() {
  python3 - "$1" <<'PY'
from urllib.parse import urlparse
import sys
raw = sys.argv[1].strip().rstrip("/")
p = urlparse(raw)
if p.scheme not in {"http", "https"} or not p.hostname:
    raise SystemExit("public URL must be an absolute http(s) origin")
if p.username or p.password or p.query or p.fragment or p.params or p.path not in {"", "/"}:
    raise SystemExit("public URL must be an origin without path/query/credentials")
if p.port is not None:
    expected = 443 if p.scheme == "https" else 80
    if p.port != expected:
        raise SystemExit(f"custom public port {p.port} is not supported by the default Caddy mapping")
host = p.hostname
if ":" in host and not host.startswith("["):
    host = f"[{host}]"
print(f"{p.scheme}://{host}")
PY
}

configure_public_url() {
  local existing_url public_url domain host_ip site_address default_url
  existing_url="$(env_get NEXT_PUBLIC_APP_URL)"
  public_url="${OTCLICK_PUBLIC_URL:-}"
  domain="${OTCLICK_DOMAIN:-}"

  if [[ -z "$public_url" && -n "$domain" ]]; then
    domain="${domain#http://}"
    domain="${domain#https://}"
    domain="${domain%%/*}"
    public_url="https://$domain"
  fi

  host_ip="${OTCLICK_HOST_IP:-$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -m1 -E '^[0-9]+\.' || true)}"
  [[ -n "$host_ip" ]] || host_ip="127.0.0.1"
  default_url="http://$host_ip"
  if [[ -n "$existing_url" && "$existing_url" != "http://localhost" && "$existing_url" != "http://localhost:3000" ]]; then
    default_url="$existing_url"
  fi

  if [[ -z "$public_url" ]]; then
    public_url="$(prompt_text 'URL приложения (https://domain или LAN http://IP)' "$default_url")"
  fi
  public_url="$(normalize_public_url "$public_url")"
  case "$public_url" in
    https://*) site_address="${public_url#https://}" ;;
    http://*) site_address=":80" ;;
    *) die "public URL must start with http:// or https://" ;;
  esac

  env_set CADDY_SITE_ADDRESS "$site_address"
  env_set SUPABASE_PUBLIC_URL "$public_url"
  env_set CORS_ORIGINS "$public_url"
  env_set NEXT_PUBLIC_SUPABASE_URL "$public_url"
  env_set NEXT_PUBLIC_API_URL "$public_url"
  env_set NEXT_PUBLIC_APP_URL "$public_url"
  env_set POLAR_SUCCESS_URL "$public_url/billing/success"
  log "public URL: $public_url"
}

configure_first_user() {
  [[ "$FRESH_ENV" -eq 1 ]] || return 0
  local email password password2
  email="${OTCLICK_ADMIN_EMAIL:-}"
  if [[ -z "$email" ]]; then
    email="$(prompt_text 'Email для входа в Otclick' 'admin@otclick.local')"
  fi
  [[ "$email" == *@*.* ]] || die "admin email looks invalid: $email"
  env_set OTCLICK_ADMIN_EMAIL "$email"

  password="${OTCLICK_ADMIN_PASSWORD:-}"
  if [[ -z "$password" && have_tty ]]; then
    password="$(prompt_secret 'Пароль Otclick (Enter = сгенерировать безопасный)')"
    if [[ -n "$password" ]]; then
      [[ ${#password} -ge 10 ]] || die "application password must be at least 10 characters"
      password2="$(prompt_secret 'Повтори пароль Otclick')"
      [[ "$password" == "$password2" ]] || die "application passwords do not match"
    fi
  fi
  if [[ -z "$password" ]]; then
    password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    GENERATED_PASSWORD="$password"
  fi
  INSTALL_ADMIN_PASSWORD="$password"
}

configure_llm() {
  local reconfigure="${OTCLICK_RECONFIGURE:-0}"
  if [[ "$FRESH_ENV" -ne 1 && "$reconfigure" != "1" ]]; then
    local existing_model existing_base
    existing_model="$(env_get OPENAI_MODEL)"
    existing_base="$(env_get OPENAI_BASE_URL)"
    if [[ -n "$(env_get OPENAI_API_KEY)" ]]; then
      LLM_VERIFY_STATUS="preserved: ${existing_model:-unknown} @ ${existing_base:-unknown}"
    else
      LLM_VERIFY_STATUS="disabled"
    fi
    return 0
  fi

  local key base model provider choice
  key="${OTCLICK_OPENAI_API_KEY:-}"
  base="${OTCLICK_OPENAI_BASE_URL:-}"
  model="${OTCLICK_OPENAI_MODEL:-}"
  provider="${OTCLICK_LLM_PROVIDER:-}"

  if [[ -z "$base" || -z "$model" ]]; then
    if ! have_tty; then
      [[ -n "$base" && -n "$model" ]] || die "non-interactive install requires OTCLICK_OPENAI_BASE_URL and OTCLICK_OPENAI_MODEL, or OTCLICK_LLM_PROVIDER=disabled"
    else
      echo >/dev/tty
      echo 'LLM provider:' >/dev/tty
      echo '  1) OpenCode Go + LongCat 2.0 (default)' >/dev/tty
      echo '  2) LongCat direct OpenAI-compatible API' >/dev/tty
      echo '  3) Custom OpenAI-compatible endpoint' >/dev/tty
      echo '  4) Disable AI for now' >/dev/tty
      choice="$(prompt_text 'Выбор' '1')"
      case "$choice" in
        1)
          provider="opencode-go"
          base="https://opencode.ai/zen/go/v1"
          model="longcat-2.0"
          echo 'OpenCode Go is technically OpenAI-compatible; its documentation targets coding-agent traffic.' >/dev/tty
          echo 'Otclick identifies itself honestly and does not impersonate a coding agent.' >/dev/tty
          ;;
        2)
          provider="longcat-direct"
          base="https://api.longcat.chat/openai/v1"
          model="$(prompt_text 'LongCat model id' 'LongCat-2.0')"
          ;;
        3)
          provider="custom"
          base="$(prompt_text 'OpenAI-compatible base URL, including /v1' '')"
          model="$(prompt_text 'Model id' '')"
          [[ -n "$base" && -n "$model" ]] || die "base URL and model id are required"
          ;;
        4)
          provider="disabled"
          base="https://api.openai.com/v1"
          model="gpt-5.4-nano"
          key=""
          ;;
        *) die "unknown LLM provider choice: $choice" ;;
      esac
    fi
  fi

  if [[ "$provider" == "disabled" || "${OTCLICK_LLM_PROVIDER:-}" == "disabled" ]]; then
    env_set OPENAI_API_KEY ""
    env_set OPENAI_BASE_URL "${base:-https://api.openai.com/v1}"
    env_set OPENAI_MODEL "${model:-gpt-5.4-nano}"
    env_set OPENAI_STRUCTURED_OUTPUT_METHOD function_calling
    LLM_VERIFY_STATUS="disabled by user"
    return 0
  fi

  if [[ -z "$key" ]]; then
    key="$(prompt_secret 'API key выбранного LLM provider')"
  fi
  [[ -n "$key" ]] || die "LLM API key is required unless AI is explicitly disabled"
  base="${base%/}"
  [[ "$base" == http://* || "$base" == https://* ]] || die "LLM base URL must start with http:// or https://"
  [[ -n "$model" ]] || die "LLM model id is required"

  env_set OPENAI_API_KEY "$key"
  env_set OPENAI_BASE_URL "$base"
  env_set OPENAI_MODEL "$model"
  env_set OPENAI_STRUCTURED_OUTPUT_METHOD function_calling

  log "verifying OpenAI-compatible chat + function calling: $model @ $base"
  if python3 infra/verify_llm.py --env .env; then
    LLM_VERIFY_STATUS="verified: $model @ $base"
  else
    LLM_VERIFY_STATUS="FAILED: $model @ $base"
    if prompt_yes_no 'LLM compatibility check failed. Continue installation anyway?' n; then
      log "warning: continuing with an unverified LLM endpoint"
    else
      die "LLM compatibility check failed; fix provider/base/model/key and rerun"
    fi
  fi
}

ensure_env() {
  if [[ ! -f .env ]]; then
    FRESH_ENV=1
    log "generating .env and cryptographic secrets"
    # Installer owns the interactive LLM questions; bootstrap only generates
    # local cryptographic secrets here.
    python3 infra/bootstrap.py --openai-key ""
  else
    log "preserving existing .env"
  fi

  env_set DISABLE_SIGNUP true
  env_set ALLOW_REAL_APPLY false
  configure_public_url
  configure_first_user
  configure_llm
}

wait_http() {
  local url="$1" name="$2" attempts="${3:-60}"
  local i
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$name is healthy"
      return 0
    fi
    sleep 2
  done
  die "$name did not become healthy: $url"
}

wait_migrate() {
  local id status i
  for ((i=1; i<=90; i++)); do
    id="$(docker compose ps -a -q migrate 2>/dev/null || true)"
    if [[ -n "$id" ]]; then
      status="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$id" 2>/dev/null || true)"
      case "$status" in
        "exited 0") log "database migrations completed"; return 0 ;;
        exited\ *) docker compose logs migrate; die "database migrations failed: $status" ;;
      esac
    fi
    sleep 2
  done
  die "database migrations did not complete"
}

find_existing_user_id() {
  local service_key response
  service_key="$(env_get SERVICE_ROLE_KEY)"
  [[ -n "$service_key" ]] || die "SERVICE_ROLE_KEY is empty"
  response="$(curl -fsS \
    -H "apikey: $service_key" \
    -H "Authorization: Bearer $service_key" \
    'http://127.0.0.1:54321/rest/v1/profiles?select=id&order=created_at.asc&limit=2')"
  python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["id"] if len(rows)==1 else ("__MULTIPLE__" if len(rows)>1 else ""))' <<<"$response"
}

sync_admin_email() {
  local user_id="$1" service_key response email
  service_key="$(env_get SERVICE_ROLE_KEY)"
  [[ -n "$service_key" ]] || die "SERVICE_ROLE_KEY is empty"
  response="$(curl -fsS \
    -H "apikey: $service_key" \
    -H "Authorization: Bearer $service_key" \
    "http://127.0.0.1:54321/auth/v1/admin/users/$user_id")"
  email="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("email", ""))' <<<"$response")"
  [[ -n "$email" ]] && env_set OTCLICK_ADMIN_EMAIL "$email"
}

create_first_user() {
  local email password service_key payload response user_id
  email="${OTCLICK_ADMIN_EMAIL:-$(env_get OTCLICK_ADMIN_EMAIL)}"
  [[ -n "$email" ]] || email="admin@otclick.local"
  password="${OTCLICK_ADMIN_PASSWORD:-$INSTALL_ADMIN_PASSWORD}"
  if [[ -z "$password" ]]; then
    password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    GENERATED_PASSWORD="$password"
  fi
  service_key="$(env_get SERVICE_ROLE_KEY)"
  [[ -n "$service_key" ]] || die "SERVICE_ROLE_KEY is empty"
  payload="$(python3 - "$email" "$password" <<'PY'
import json,sys
print(json.dumps({"email": sys.argv[1], "password": sys.argv[2], "email_confirm": True}))
PY
)"
  log "creating the single application user: $email"
  response="$(curl -fsS -X POST \
    -H "apikey: $service_key" \
    -H "Authorization: Bearer $service_key" \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    'http://127.0.0.1:54321/auth/v1/admin/users')"
  user_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))' <<<"$response")"
  [[ -n "$user_id" ]] || die "auth admin API did not return a user id"
  env_set OTCLICK_USER_ID "$user_id"
  env_set OTCLICK_ADMIN_EMAIL "$email"
}

ensure_single_user() {
  local user_id existing
  user_id="${OTCLICK_USER_ID:-$(env_get OTCLICK_USER_ID)}"
  if [[ -n "${OTCLICK_USER_ID:-}" ]]; then
    env_set OTCLICK_USER_ID "$user_id"
  fi
  if [[ -z "$user_id" ]]; then
    existing="$(find_existing_user_id)"
    if [[ "$existing" == "__MULTIPLE__" ]]; then
      die "multiple profiles exist; set OTCLICK_USER_ID explicitly before continuing"
    elif [[ -n "$existing" ]]; then
      user_id="$existing"
      env_set OTCLICK_USER_ID "$user_id"
      log "reusing the only existing profile: $user_id"
    else
      create_first_user
      user_id="$(env_get OTCLICK_USER_ID)"
    fi
  else
    log "using configured single user: $user_id"
  fi

  sync_admin_email "$user_id"
  log "loading curated candidate profile/facts"
  docker compose exec -T api python scripts/load_candidate_data.py --user-id "$user_id"
}

check_candidate_profile() {
  local report
  report="$(python3 - "$INSTALL_DIR" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
profile_path = root / "backend/data/candidate/candidate_profile.json"
facts_path = root / "backend/data/candidate/confirmed_facts.json"
required = [
    "target_roles", "next_role_priorities", "not_interested", "organization_level",
    "industries", "business_scale", "compensation", "positioning",
    "strong_role_signals", "weak_role_signals",
]
errors = []
try:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
except Exception as exc:
    profile = {}
    errors.append(f"candidate_profile.json unreadable: {exc}")
try:
    facts_doc = json.loads(facts_path.read_text(encoding="utf-8"))
except Exception as exc:
    facts_doc = {}
    errors.append(f"confirmed_facts.json unreadable: {exc}")

missing = [key for key in required if not profile.get(key)]
if missing:
    errors.append("empty required profile sections: " + ", ".join(missing))
facts = facts_doc.get("facts") if isinstance(facts_doc, dict) else None
if not isinstance(facts, list) or not facts:
    errors.append("confirmed_facts.json has no facts")
    facts = []
for i, fact in enumerate(facts, 1):
    if not isinstance(fact, dict) or not fact.get("key") or not fact.get("statement"):
        errors.append(f"fact #{i} requires key and statement")

print("OK" if not errors else "ACTION_REQUIRED")
print(len(facts))
for err in errors:
    print(err)
PY
)"
  local state count
  state="$(sed -n '1p' <<<"$report")"
  count="$(sed -n '2p' <<<"$report")"
  if [[ "$state" == "OK" ]]; then
    PROFILE_STATUS="complete; $count confirmed facts loaded; no mandatory manual completion"
  else
    PROFILE_STATUS="ACTION REQUIRED; see files below"
    log "candidate profile validation requires attention:"
    sed -n '3,$p' <<<"$report" | sed 's/^/[otclick]   - /'
  fi
}

start_stack() {
  log "validating docker compose configuration"
  docker compose config >/dev/null
  log "building and starting stack"
  docker compose up -d --build
  wait_migrate
  wait_http http://127.0.0.1:8000/health backend 90
  wait_http http://127.0.0.1:3000 frontend 90
  wait_http http://127.0.0.1:54321/auth/v1/health Supabase-auth 90
}

print_profile_instructions() {
  local user_id
  user_id="$(env_get OTCLICK_USER_ID)"
  echo
  echo "Candidate profile: $PROFILE_STATUS"
  echo "Files used by scorer / cover writer:"
  echo "  $INSTALL_DIR/backend/data/candidate/candidate_profile.json"
  echo "    Edit only when needed: target_roles, next_role_priorities, not_interested,"
  echo "    organization_level, industries, business_scale, compensation, positioning,"
  echo "    strong_role_signals, weak_role_signals and claim/positioning constraints."
  echo "  $INSTALL_DIR/backend/data/candidate/confirmed_facts.json"
  echo "    Add only confirmed facts. Each fact needs key + statement; keep metrics/tags"
  echo "    and any claim guardrails precise. Do not add assumptions as confirmed facts."
  echo "After editing either file, reload prepared candidate data with:"
  echo "  cd $INSTALL_DIR && docker compose exec -T api python scripts/load_candidate_data.py --user-id '$user_id'"
  echo "The current bundled profile is already curated; edit it only if the summary above says ACTION REQUIRED or you want to change your positioning/facts."
}

print_result() {
  local public_url email current_sha model base
  public_url="$(env_get NEXT_PUBLIC_APP_URL)"
  email="$(env_get OTCLICK_ADMIN_EMAIL)"
  current_sha="$(git rev-parse HEAD)"
  model="$(env_get OPENAI_MODEL)"
  base="$(env_get OPENAI_BASE_URL)"
  echo
  echo "============================================================"
  echo "Otclick-hh installed/updated"
  echo "URL:      $public_url"
  echo "Login:    $email"
  if [[ -n "$GENERATED_PASSWORD" ]]; then
    echo "Password: $GENERATED_PASSWORD"
    echo "Save this password now; installer does not store it in .env."
  elif [[ "$FRESH_ENV" -eq 1 ]]; then
    echo "Password: the password you entered during installation"
  else
    echo "Password: existing account password"
  fi
  echo "Revision: $current_sha"
  echo "LLM:      $LLM_VERIFY_STATUS"
  [[ -n "$model" ]] && echo "Model:    $model"
  [[ -n "$base" ]] && echo "LLM URL:  $base"
  echo "Real HH submit: DISABLED by default (ALLOW_REAL_APPLY=false)"
  echo "Log:      $LOG_FILE"
  [[ -n "$BACKUP_FILE" ]] && echo "Backup:   $BACKUP_FILE"
  if [[ "$public_url" == http://* && "$public_url" != "http://localhost" && "$public_url" != "http://127.0.0.1" ]]; then
    echo "WARNING: remote HTTP is suitable only for temporary/LAN testing. Rerun with OTCLICK_RECONFIGURE=1 and a https:// domain for Internet exposure."
  fi
  echo "============================================================"
  print_profile_instructions
}

main() {
  require_ubuntu
  install_packages
  checkout_repo
  ensure_env
  start_stack
  ensure_single_user
  check_candidate_profile
  print_result
}

main "$@"
