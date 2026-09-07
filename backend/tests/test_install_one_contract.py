from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_one_command_installer_preserves_existing_docker_stack():
    wrapper = (ROOT / "install-one.sh").read_text(encoding="utf-8")

    assert 'docker compose version >/dev/null 2>&1' in wrapper
    assert 'using existing Docker + Compose; package stack left untouched' in wrapper
    assert 'apt-get install -y git curl ca-certificates python3 docker.io' in wrapper  # old block matched for replacement
    assert 'apt-get install -y git curl ca-certificates python3\n' in wrapper  # replacement has no Docker package


def test_one_command_installer_keeps_docker_package_families_separate():
    wrapper = (ROOT / "install-one.sh").read_text(encoding="utf-8")

    assert 'docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin' in wrapper
    assert 'apt-get install -y docker.io docker-compose-v2' in wrapper
    assert 'Docker CE/containerd.io detected; installing matching Compose plugin' in wrapper
    assert 'Ubuntu docker.io detected; installing matching Compose v2 package' in wrapper


def test_one_command_installer_refuses_blind_patch_if_base_installer_changes():
    wrapper = (ROOT / "install-one.sh").read_text(encoding="utf-8")

    assert 'installer prerequisite block changed; refusing an unsafe blind patch' in wrapper
    assert 'exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL"' in wrapper
