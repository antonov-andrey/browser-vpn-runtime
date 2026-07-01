"""Tests for Playwright MCP runtime launcher."""

import json
from pathlib import Path

import pytest

from browser_vpn_runtime.playwright_mcp import PlaywrightMcpConfig, _args_parse, playwright_mcp_command_argv_get
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
    source_profile_path = data_source_path / "playwright_profile"
    source_profile_path.mkdir()
    (source_profile_path / "Preferences").write_text("prefs", encoding="utf-8")
    return data_source_path


def test_playwright_mcp_command_uses_runtime_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Build Playwright MCP argv through the browser/VPN runtime boundary."""
    data_source_path = _runtime_data_source_create(tmp_path)
    persistent_profile_path = tmp_path / "runtime-profile"
    output_dir = tmp_path / "playwright-output"
    mcp_config_path = tmp_path / "mcp" / "config.json"
    monkeypatch.setattr(BrowserRuntime, "_have_tun_route", lambda self: True)
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        locale="tr-TR",
        mcp_config_path=mcp_config_path,
        output_dir=output_dir,
        persistent_profile_path=persistent_profile_path,
        port=12000,
        timezone="Europe/Istanbul",
        viewport_height=900,
        viewport_width=1440,
    )

    command_argv = playwright_mcp_command_argv_get(config)

    assert command_argv == [
        "xvfb-run",
        "-a",
        "playwright-mcp",
        "--allowed-hosts",
        "localhost,localhost:12000,127.0.0.1,127.0.0.1:12000",
        "--config",
        str(mcp_config_path),
        "--host",
        "127.0.0.1",
        "--port",
        "12000",
    ]
    config_payload = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    assert config_payload["browser"]["launchOptions"]["headless"] is False
    assert config_payload["browser"]["contextOptions"]["locale"] == "tr-TR"
    assert config_payload["browser"]["contextOptions"]["timezoneId"] == "Europe/Istanbul"
    assert config_payload["browser"]["contextOptions"]["viewport"] == {"height": 900, "width": 1440}
    assert config_payload["browser"]["userDataDir"] == str(persistent_profile_path)
    assert config_payload["sharedBrowserContext"] is True
    assert mcp_config_path.with_suffix(".stealth.js").is_file()
    assert (persistent_profile_path / "Preferences").read_text(encoding="utf-8") == "prefs"
    assert output_dir.is_dir()


def test_playwright_mcp_command_declares_allowed_hosts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Allow workflow containers to reach the MCP server through a runtime-owned service host."""
    data_source_path = _runtime_data_source_create(tmp_path)
    monkeypatch.setattr(BrowserRuntime, "_have_tun_route", lambda self: True)
    config = PlaywrightMcpConfig(
        allowed_host_list=["localhost", "127.0.0.1", "openvpn"],
        data_source_path=data_source_path,
        host="0.0.0.0",
        mcp_config_path=tmp_path / "mcp" / "config.json",
        output_dir=tmp_path / "playwright-output",
        persistent_profile_path=tmp_path / "runtime-profile",
    )

    command_argv = playwright_mcp_command_argv_get(config)

    assert "--allowed-hosts" in command_argv
    assert (
        command_argv[command_argv.index("--allowed-hosts") + 1]
        == "localhost,localhost:8931,127.0.0.1,127.0.0.1:8931,openvpn,openvpn:8931"
    )


def test_playwright_mcp_cli_maps_allowed_hosts_to_config_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Parse allowed MCP hosts into the validated config field."""
    argv = [
        "browser-vpn-runtime-playwright-mcp",
        "--allowed-hosts",
        "localhost,127.0.0.1,openvpn",
        "--data-source-path",
        str(tmp_path / "data-source"),
    ]
    monkeypatch.setattr("sys.argv", argv)

    namespace = _args_parse()

    assert namespace.allowed_host_list == ["localhost", "127.0.0.1", "openvpn"]
    assert "allowed_hosts" not in vars(namespace)


def test_playwright_mcp_command_allows_no_vpn_data_source(tmp_path: Path) -> None:
    """Build Playwright MCP argv when the DataSource has no OpenVPN metadata."""
    data_source_path = tmp_path / "data-source"
    data_source_path.mkdir()
    persistent_profile_path = tmp_path / "runtime-profile"
    output_dir = tmp_path / "playwright-output"
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        mcp_config_path=tmp_path / "mcp" / "config.json",
        output_dir=output_dir,
        persistent_profile_path=persistent_profile_path,
    )

    command_argv = playwright_mcp_command_argv_get(config)

    assert command_argv[:3] == ["xvfb-run", "-a", "playwright-mcp"]
    assert output_dir.is_dir()
