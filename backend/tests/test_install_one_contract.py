from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _wrapper() -> str:
    return (ROOT / "install-one.sh").read_text(encoding="utf-8")


def test_one_command_installer_preserves_existing_docker_stack():
    wrapper = _wrapper()

    assert 'docker compose version >/dev/null 2>&1' in wrapper
    assert 'existing Docker + Compose detected; package stack left untouched' in wrapper
    assert 'apt-get install -y git curl ca-certificates python3 docker.io' in wrapper  # old block matched for replacement
    assert 'apt-get install -y git curl ca-certificates python3 zstd >>"$LOG_FILE" 2>&1' in wrapper


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
    assert 'docker compose build api frontend >>"$LOG_FILE" 2>&1' in wrapper  # emergency override only
    assert 'details -> $LOG_FILE' in wrapper
    assert 'trap - ERR' in wrapper


def test_one_command_installer_prints_compose_failure_diagnostics_without_tailing_its_own_log():
    wrapper = _wrapper()

    assert 'diagnose_stack() {' in wrapper
    assert 'compose_or_diagnose() {' in wrapper
    assert 'docker compose ps -a >&2' in wrapper
    assert 'docker compose logs --no-color --tail=120 "$service" >&2' in wrapper
    assert "state_error=\"$(docker inspect -f '{{.State.Error}}'" in wrapper
    assert 'error=${state_error:-none}' in wrapper
    assert 'docker compose command failed:' in wrapper
    assert 'tail -n 30 "$LOG_FILE"' not in wrapper


def test_one_command_installer_uses_exact_sha_public_prerelease_by_default():
    wrapper = _wrapper()

    assert 'git_sha="$(git rev-parse HEAD)"' in wrapper
    assert 'release_tag="install-${git_sha}"' in wrapper
    assert 'https://github.com/gest0r1/Otclick-hh/releases/download/${release_tag}' in wrapper
    assert '"${release_base}/manifest.json"' in wrapper
    assert '"${release_base}/SHA256SUMS"' in wrapper
    assert 'manifest.get("git_sha", "")' in wrapper
    assert 'sha256sum "$bundle_file"' in wrapper
    assert 'zstd -d -c "$bundle_file" | docker load' in wrapper


def test_one_command_installer_does_not_build_locally_unless_explicitly_overridden():
    wrapper = _wrapper()

    assert 'OTCLICK_ALLOW_LOCAL_BUILD:-0' in wrapper
    assert '[7/8] emergency local build enabled' in wrapper
    assert 'Local build is intentionally disabled on low-memory hosts.' in wrapper
    assert 'Emergency override: OTCLICK_ALLOW_LOCAL_BUILD=1' in wrapper
    assert '[7/8] downloading prebuilt Otclick images (~1 GiB, no local build)' in wrapper


def test_one_command_installer_never_pulls_local_backend_as_external_image():
    wrapper = _wrapper()

    assert 'docker compose pull db migrate auth rest realtime storage storage-init kong caddy' in wrapper
    assert 'docker compose pull api' not in wrapper
    assert 'docker compose pull worker' not in wrapper
    assert 'docker compose up -d --build' in wrapper  # old start_stack block matched exactly


def test_installer_starts_infra_then_forces_only_current_app_images():
    wrapper = _wrapper()

    assert 'compose_or_diagnose up -d --no-build --pull never \\\n    db migrate auth rest realtime storage storage-init kong' in wrapper
    assert '--force-recreate --no-deps api frontend' in wrapper
    assert '--force-recreate --no-deps worker' in wrapper
    assert '--force-recreate --no-deps worker caddy' not in wrapper
    assert 'compose_or_diagnose up -d --no-build --pull never --no-deps caddy' in wrapper
    assert 'Caddy contains no application code' in wrapper
    assert 'Keep an already-running proxy stable' in wrapper
    assert 'Docker Compose does not reliably recreate an existing container' in wrapper
    assert 'without bouncing DB' in wrapper


def test_candidate_overrides_stay_private_but_readable_by_container_user():
    wrapper = _wrapper()

    assert 'chown 1000:1000 "$CANDIDATE_LOCAL_DIR"' in wrapper
    assert 'chmod 700 "$CANDIDATE_LOCAL_DIR"' in wrapper
    assert 'chmod 600 "$CANDIDATE_LOCAL_DIR/candidate_profile.json"' in wrapper
    assert 'backend/Dockerfile runs the API/worker as uid:gid 1000:1000' in wrapper


def test_candidate_reload_uses_runtime_mount_without_building_images():
    wrapper = _wrapper()

    assert "docker compose exec -T api python scripts/load_candidate_data.py" in wrapper
    assert "docker compose up -d --build api worker && docker compose exec" in wrapper  # old line matched for replacement


def test_one_command_installer_refuses_blind_patch_if_base_installer_changes():
    wrapper = _wrapper()

    assert 'refusing an unsafe blind patch' in wrapper
    assert 'exec env OTCLICK_REF="$REF" bash "$TMP_INSTALL"' in wrapper
