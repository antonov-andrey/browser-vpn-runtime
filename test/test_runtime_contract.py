"""Tests for runtime readiness contract."""

from pathlib import Path

import pytest

from browser_vpn_runtime.config import BrowserRuntimeConfig
from browser_vpn_runtime.runtime import BrowserRuntime


def _runtime_data_source_create(tmp_path: Path) -> Path:
    """Create a minimal browser/VPN DataSource fixture.

    Args:
        tmp_path: Pytest temporary path.

    Returns:
        Fixture DataSource path.
    """

    data_source_path = tmp_path / "data-source"
    openvpn_path = data_source_path / "openvpn"
    openvpn_path.mkdir(parents=True)
    (openvpn_path / "config.json").write_text(
        '{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}\n',
        encoding="utf-8",
    )
    (openvpn_path / "client.ovpn").write_text("client\n", encoding="utf-8")
    return data_source_path


def test_browser_runtime_reports_ready_when_boundaries_exist(tmp_path: Path) -> None:
    """Return a readiness state when OpenVPN and profile prerequisites exist."""
    data_source_path = _runtime_data_source_create(tmp_path)
    runtime_profile_path = tmp_path / "runtime-profile"
    config = BrowserRuntimeConfig(
        data_source_path=data_source_path,
        openvpn_config_name="client.ovpn",
        persistent_profile_path=runtime_profile_path,
    )

    state = BrowserRuntime(config).readiness_check()

    assert state.is_ready is True
    assert state.openvpn_config_name == "client.ovpn"
    assert state.persistent_profile_path == runtime_profile_path


def test_browser_runtime_reports_ready_when_profile_dir_is_missing(tmp_path: Path) -> None:
    """Treat an absent DataSource profile directory as an empty first-run profile."""
    data_source_path = _runtime_data_source_create(tmp_path)
    config = BrowserRuntimeConfig(data_source_path=data_source_path, openvpn_config_name="client.ovpn")

    state = BrowserRuntime(config).readiness_check()

    assert state.is_ready is True
    assert state.problem_list == []


def test_browser_runtime_context_materializes_profile_and_settings(tmp_path: Path) -> None:
    """Return profile path and browser settings for caller-owned Playwright launch."""
    data_source_path = _runtime_data_source_create(tmp_path)
    source_profile_path = data_source_path / "playwright_profile"
    source_profile_path.mkdir()
    (source_profile_path / "Preferences").write_text("prefs", encoding="utf-8")
    runtime_profile_path = tmp_path / "runtime-profile"
    config = BrowserRuntimeConfig(
        data_source_path=data_source_path,
        locale="tr-TR",
        openvpn_config_name="client.ovpn",
        persistent_profile_path=runtime_profile_path,
        timezone="Europe/Istanbul",
        viewport_height=900,
        viewport_width=1440,
    )

    context = BrowserRuntime(config).playwright_runtime_context_get()

    assert context.locale == "tr-TR"
    assert context.materialized_profile_file_path_list == [runtime_profile_path / "Preferences"]
    assert context.persistent_profile_path == runtime_profile_path
    assert context.timezone == "Europe/Istanbul"
    assert context.viewport_height == 900
    assert context.viewport_width == 1440


def test_browser_runtime_reports_missing_required_vpn_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Report missing tun0 when a workflow requires VPN route visibility."""
    data_source_path = _runtime_data_source_create(tmp_path)
    config = BrowserRuntimeConfig(
        data_source_path=data_source_path,
        openvpn_config_name="client.ovpn",
        require_vpn_route=True,
    )
    monkeypatch.setattr(BrowserRuntime, "_have_tun_route", lambda self: False)

    state = BrowserRuntime(config).readiness_check()

    assert state.is_ready is False
    assert "vpn_route: tun0 route is not visible in the current network namespace" in state.problem_list
