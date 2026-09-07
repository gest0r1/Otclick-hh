from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_linux_artifact_contains_prebuilt_app_images_and_integrity_metadata():
    workflow = (ROOT / ".github/workflows/build-artifact.yml").read_text(encoding="utf-8")

    assert "docker compose build api frontend" in workflow
    assert "docker save" in workflow
    assert "aiautoclicker-backend:latest" in workflow
    assert "aiautoclicker-frontend:latest" in workflow
    assert "otclick-images-linux-amd64.tar.zst" in workflow
    assert "manifest.json" in workflow
    assert "SHA256SUMS" in workflow


def test_linux_artifact_uses_openchamber_style_upload_with_short_retention():
    workflow = (ROOT / ".github/workflows/build-artifact.yml").read_text(encoding="utf-8")

    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "name: otclick-linux-amd64-${{ github.sha }}" in workflow
    assert "retention-days: 7" in workflow
    assert "compression-level: 0" in workflow
    assert "if-no-files-found: error" in workflow


def test_linux_artifact_never_publishes_runtime_secrets():
    workflow = (ROOT / ".github/workflows/build-artifact.yml").read_text(encoding="utf-8")

    assert "cp install.sh install-one.sh docker-compose.yml artifacts/" in workflow
    assert "cp .env" not in workflow
    assert "OPENAI_API_KEY" not in workflow
    assert "SERVICE_ROLE_KEY" not in workflow
    assert "FERNET_KEY" not in workflow
