"""Smoke test for the Render deployment artifacts.

These tests don't deploy anything; they just verify the configuration
files exist and have the expected shape so an accidental deletion
fails CI instead of breaking production.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_render_yaml_exists() -> None:
    assert (REPO_ROOT / "render.yaml").is_file(), "render.yaml missing"


def test_render_yaml_declares_web_service() -> None:
    doc = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    services = doc.get("services")
    assert services, "render.yaml has no services"
    assert services[0]["type"] == "web", "first service must be a Web Service"
    assert services[0]["healthCheckPath"] == "/health"
    assert "TELEGRAM_BOT_TOKEN" in {e["key"] for e in services[0]["envVars"]}
    assert "SERPER_API_KEY" in {e["key"] for e in services[0]["envVars"]}


def test_render_yaml_uses_persistent_disk() -> None:
    doc = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    disk = doc["services"][0].get("disk")
    assert disk is not None, (
        "render.yaml has no disk — SQLite history will be wiped on every deploy"
    )
    assert disk["mountPath"] == "/var/data"
    assert disk["sizeGB"] >= 1


def test_render_yaml_start_command_uses_main_module() -> None:
    doc = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    cmd = doc["services"][0]["startCommand"]
    assert "instagram_dork_bot" in cmd


def test_requirements_txt_matches_pyproject() -> None:
    """Both files list the same runtime deps so Render's `pip install`
    doesn't accidentally diverge from `pip install -e .[dev]`."""
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = {
        dep.split(">=")[0].split("==")[0].strip() for dep in pyproject["project"]["dependencies"]
    }
    actual = {
        line.split(">=")[0].split("==")[0].strip()
        for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    missing = expected - actual
    assert not missing, f"requirements.txt is missing deps: {sorted(missing)}"


def test_dockerfile_present() -> None:
    assert (REPO_ROOT / "Dockerfile").is_file()
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:" in content
    assert "instagram_dork_bot" in content
    assert "EXPOSE 10000" in content


def test_dockerignore_present() -> None:
    assert (REPO_ROOT / ".dockerignore").is_file()
    content = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    # Must exclude dev artifacts so the image stays small.
    for needle in ("__pycache__", "tests/", ".env", ".git/"):
        assert needle in content, f".dockerignore missing {needle}"


def test_runtime_txt_pins_python_311() -> None:
    content = (REPO_ROOT / "runtime.txt").read_text(encoding="utf-8").strip()
    assert content.startswith("python-3.11"), f"runtime.txt pins unexpected version: {content}"


def test_render_md_documents_persistent_disk_caveat() -> None:
    content = (REPO_ROOT / "RENDER.md").read_text(encoding="utf-8")
    assert "persistent disk" in content.lower()
    assert "free" in content.lower()
    assert "TELEGRAM_BOT_TOKEN" in content


def test_env_example_masks_real_keys() -> None:
    """The shipped .env.example must never carry a real key."""
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    # Real Serper keys are 40 hex chars after the `key=` portion.
    # We only need a smoke check: no value should be longer than a
    # username / id stub.
    for line in content.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        value = line.split("=", 1)[1].strip()
        if not value:
            continue
        # Anything that looks like an API key is suspicious.
        assert len(value) < 40, f".env.example has a long value on line: {line!r}"
