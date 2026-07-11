"""Tests for the browser launch-context contract."""

from pathlib import Path
import tomllib

from browser_vpn_runtime.config import BrowserLocaleConfig, BrowserRuntimeConfig
from browser_vpn_runtime.runtime import BrowserRuntime


def test_browser_runtime_has_no_fake_readiness_api_or_default_command() -> None:
    """Leave readiness to the launcher TCP check and platform healthcheck."""

    project_root = Path(__file__).resolve().parents[1]
    project_metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert not hasattr(BrowserRuntime, "readiness_check")
    assert "browser-vpn-runtime-readiness" not in project_metadata["project"]["scripts"]
    assert "CMD" not in (project_root / "docker/playwright/Dockerfile").read_text(encoding="utf-8")


def test_browser_runtime_config_has_no_derived_profile_proxies() -> None:
    """Keep profile locations in explicit configuration and launcher inputs."""

    assert not hasattr(BrowserRuntimeConfig, "codex_profile_path")
    assert not hasattr(BrowserRuntimeConfig, "playwright_profile_path")


def test_browser_runtime_context_materializes_profile_and_settings(tmp_path: Path) -> None:
    """Return profile path and browser settings without VPN metadata."""
    data_source_path = tmp_path / "data-source"
    source_profile_path = data_source_path / "playwright_profile"
    source_profile_path.mkdir(parents=True)
    (source_profile_path / "Preferences").write_text("prefs", encoding="utf-8")
    runtime_profile_path = tmp_path / "runtime-profile"
    config = BrowserRuntimeConfig(
        data_source_path=data_source_path,
        locale_config=BrowserLocaleConfig(locale="tr-TR"),
        persistent_profile_path=runtime_profile_path,
        timezone="Europe/Istanbul",
        viewport_height=900,
        viewport_width=1440,
    )

    context = BrowserRuntime(config).playwright_runtime_context_get()

    assert context.locale_config == BrowserLocaleConfig(locale="tr-TR")
    assert context.materialized_profile_file_path_list == [runtime_profile_path / "Preferences"]
    assert context.persistent_profile_path == runtime_profile_path
    assert context.timezone == "Europe/Istanbul"
    assert context.viewport_height == 900
    assert context.viewport_width == 1440
