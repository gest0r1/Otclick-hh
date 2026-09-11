from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_installer_routes_existing_opt_install_to_incremental_updater():
    installer = _read("install.sh")
    assert 'DEFAULT_TARGET_DIR="/opt/otclick-hh"' in installer
    assert 'if [[ -d "$TARGET_DIR/.git"' in installer
    assert 'install-update.sh' in installer
    assert 'OTCLICK_FULL_INSTALL' in installer
    route_pos = installer.index('if [[ -d "$TARGET_DIR/.git"')
    bundle_pos = installer.index('otclick-images-linux-amd64.tar.gz')
    assert route_pos < bundle_pos


def test_incremental_updater_never_downloads_combined_bundle_or_builds_locally():
    updater = _read("install-update.sh")
    assert 'OTCLICK_REF:-main' in updater
    assert 'OTCLICK_DIR:-/opt/otclick-hh' in updater
    assert 'docker-compose.prebuilt.yml' in updater
    assert 'docker pull "$image_ref"' in updater
    assert 'GHCR pull complete (cached layers reused)' in updater
    assert 'otclick-images-linux-amd64.tar.gz' not in updater
    assert 'docker compose build' not in updater
    assert 'docker build' not in updater
    assert '--no-build' in updater


def test_incremental_updater_recovers_pruned_component_and_preserves_env():
    updater = _read("install-update.sh")
    assert '[[ -f "$INSTALL_DIR/.env" ]]' in updater
    assert 'restore the original .env before updating' in updater
    assert 'docker image inspect aiautoclicker-backend:latest' in updater
    assert 'docker image inspect aiautoclicker-frontend:latest' in updater
    assert 'BACKEND_CHANGED=1' in updater
    assert 'FRONTEND_CHANGED=1' in updater
    assert 'infra/bootstrap.py' not in updater


def test_incremental_updater_uses_release_fallback_only_after_ghcr_failure():
    updater = _read("install-update.sh")
    ghcr = updater.index('docker pull "$image_ref"')
    fallback = updater.index('using component Release fallback')
    zstd = updater.index('ensure_zstd')
    assert ghcr < fallback
    assert zstd < fallback  # helper is defined before use
    assert 'command -v zstd' in updater


def test_artifact_workflow_is_content_addressed_and_tests_public_ghcr():
    workflow = _read(".github/workflows/build-artifact.yml")
    assert 'packages: write' in workflow
    assert 'BACKEND_HASH' in workflow
    assert 'FRONTEND_HASH' in workflow
    assert 'ghcr.io/${owner}/otclick-hh-backend:component-${BACKEND_HASH}' in workflow
    assert 'ghcr.io/${owner}/otclick-hh-frontend:component-${FRONTEND_HASH}' in workflow
    assert 'Verify anonymous GHCR pulls' in workflow
    assert 'docker logout ghcr.io' in workflow
    assert 'docker pull "$BACKEND_IMAGE"' in workflow
    assert 'docker pull "$FRONTEND_IMAGE"' in workflow
    assert '"schema_version": 2' in workflow
    assert '"fresh_install_bundle": "otclick-images-linux-amd64.tar.gz"' in workflow


def test_frontend_runtime_injection_is_part_of_production_contract():
    workflow = _read(".github/workflows/build-artifact.yml")
    override = _read("docker-compose.prebuilt.yml")
    injector = _read("infra/frontend-runtime-env.sh")
    assert 'NEXT_PUBLIC_SUPABASE_URL' in override
    assert 'frontend-runtime-env.sh' in override
    assert 'Smoke test frontend runtime configuration' in workflow
    assert 'otclick-runtime-supabase.invalid' in workflow
    assert 'otclick-runtime-supabase.invalid' in injector
    assert '__OTCLICK_SUPABASE_ANON_KEY__' in injector


def test_exact_release_checksum_contract_matches_fresh_and_incremental_clients():
    workflow = _read(".github/workflows/build-artifact.yml")
    fresh = _read("install.sh")
    updater = _read("install-update.sh")
    assert '(cd artifacts && sha256sum manifest.json)' in workflow
    assert 'sha256sum fresh-install/otclick-images-linux-amd64.tar.gz' in workflow
    assert 'sha256sum -c SHA256SUMS' in fresh
    assert 'expected_manifest=' in updater
