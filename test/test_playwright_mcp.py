"""Tests for Playwright MCP runtime launcher."""

from pathlib import Path

from browser_vpn_runtime.playwright_mcp import PlaywrightMcpConfig, playwright_mcp_command_argv_get


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


def test_playwright_mcp_command_uses_runtime_context(tmp_path: Path) -> None:
    """Build Playwright MCP argv through the browser/VPN runtime boundary."""
    data_source_path = _runtime_data_source_create(tmp_path)
    persistent_profile_path = tmp_path / "runtime-profile"
    output_dir = tmp_path / "playwright-output"
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        locale="tr-TR",
        output_dir=output_dir,
        persistent_profile_path=persistent_profile_path,
        timezone="Europe/Istanbul",
        viewport_height=900,
        viewport_width=1440,
    )

    command_argv = playwright_mcp_command_argv_get(config)

    assert command_argv == [
        "npx",
        "--yes",
        "@playwright/mcp@latest",
        "--browser",
        "chrome",
        "--no-sandbox",
        "--user-data-dir",
        str(persistent_profile_path),
        "--viewport-size",
        "1440x900",
        "--output-dir",
        str(output_dir),
        "--output-mode",
        "file",
        "--headless",
    ]
    assert (persistent_profile_path / "Preferences").read_text(encoding="utf-8") == "prefs"
    assert output_dir.is_dir()
