from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_env_defaults_keep_signup_and_real_apply_closed():
    env = _read(".env.example")

    assert "DISABLE_SIGNUP=true" in env
    assert "ALLOW_REAL_APPLY=false" in env
    assert "OTCLICK_USER_ID=" in env
    assert "CADDY_SITE_ADDRESS=:80" in env


def test_compose_exposes_only_caddy_publicly():
    compose = _read("docker-compose.yml")

    assert 'GOTRUE_DISABLE_SIGNUP: "${DISABLE_SIGNUP:-true}"' in compose
    assert '"127.0.0.1:54321:8000"' in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"127.0.0.1:3000:3000"' in compose
    assert '"80:80"' in compose
    assert '"443:443"' in compose
    assert '"443:443/udp"' in compose


def test_caddy_keeps_browser_on_one_origin():
    caddy = _read("infra/Caddyfile")

    assert "reverse_proxy api:8000" in caddy
    assert "reverse_proxy kong:8000" in caddy
    assert "reverse_proxy frontend:3000" in caddy
    assert "/api/*" in caddy
    assert "/auth/*" in caddy
    assert "/rest/*" in caddy
    assert "/storage/*" in caddy


def test_backend_image_contains_prepared_candidate_loader():
    dockerfile = _read("backend/Dockerfile")

    assert "COPY backend/data ./data" in dockerfile
    assert "COPY backend/scripts ./scripts" in dockerfile


def test_update_backs_up_before_fetch_and_preserves_env_secrets():
    installer = _read("install.sh")

    backup_pos = installer.index("backup_existing")
    fetch_pos = installer.index('git fetch --prune origin')
    assert backup_pos < fetch_pos
    assert "infra/bootstrap.py --force" not in installer
    assert 'log "preserving existing .env"' in installer
    assert "pg_dump -U postgres -d postgres -Fc" in installer


def test_installer_creates_one_user_via_admin_and_loads_candidate_context():
    installer = _read("install.sh")

    assert "GOTRUE" not in installer  # auth configuration stays in compose, not shell mutation
    assert "auth/v1/admin/users" in installer
    assert 'env_set DISABLE_SIGNUP true' in installer
    assert "scripts/load_candidate_data.py --user-id" in installer
    assert "multiple profiles exist" in installer


def test_installer_never_enables_real_apply():
    installer = _read("install.sh")

    assert "ALLOW_REAL_APPLY=true" not in installer
    assert "Real HH submit: DISABLED by default" in installer
