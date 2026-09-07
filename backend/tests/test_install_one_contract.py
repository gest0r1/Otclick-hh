from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _wrapper() -> str:
    return (ROOT / "install-one.sh").read_text(encoding="utf-8")


def test_one_command_installer_preserves_existing_docker_stack():
    wrapper = _wrapper()

    assert 'docker compose version >/dev/null 2>&1' in wrapper
    assert 'existing Docker + Compose detected; package stack left untouched' in wrapper
    assert 'apt-get install -y git curl ca-certificates python3 docker.io' in wrapper  # old block matched for replacement
    assert 'apt-get install -y git curl ca-certificates python3 >>"$LOG_FILE" 2>&1' in wrapper


def test_one_command_installer_keeps_docker_package_families_separate():
    wrapper = _wrapper()

    assert 'docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin' in wrapper
    assert 'apt-get install -y docker.io docker-compose-v2' in wrapper
    assert 'Docker CE/containerd.io detected; installing matching Compose plugin' in wrapper
    assert 'Ubuntu docker.io detected; installing matching Compose v2 package' in wrapper


def test_one_command_installer_hides_noisy_package_and_docker_progress():
    wrapper = _wrapper()

    assert 'apt-get update >>"$LOG_FILE" 2>&1' in wrapper
    assert 'docker compose pull db migrate auth rest realtime storage storage-init kong caddy >>"$LOG_FILE" 2>&1' in wrapper
    assert 'docker compose build api frontend >>"$LOG_FILE" 2>&1' in wrapper
    assert 'docker compose up -d --no-build --pull never >>"$LOG_FILE" 2>&1' in wrapper
    assert 'details -> $LOG_FILE' in wrapper
    assert 'tail -n 30 "$LOG_FILE"' in wrapper


def test_one_command_installer_never_pulls_local_backend_as_external_image():
    wrapper = _wrapper()

    assert 'docker compose pull db migrate auth rest realtime storage storage-init kong caddy' in wrapper
    assert 'docker compose pull api' not in wrapper
    assert 'docker compose pull worker' not in wrapper
    assert 'docker compose build api frontend' in wrapper
    assert 'docker compose up -d --build' in wrapper  # old start_stack block matched exactly
    assert 'docker compose up -d --no-build --pull never' in wrapper


def test_one_command_installer_refuses_blind_patch_if_base_installer_changes():
    wrapper = _wrapper()

    assert 'refusing an unsafe blind patch' in wrapper
    assert 'exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL"' in wrapper
