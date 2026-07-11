"""Tests for Playwright MCP runtime launcher."""

import json
import socket
from pathlib import Path

import pytest

from browser_vpn_runtime.config import BrowserLocaleConfig
from browser_vpn_runtime import playwright_mcp
from browser_vpn_runtime.playwright_mcp import PlaywrightMcpConfig, _args_parse, playwright_mcp_command_argv_get


class _ReadyProxyConnection:
    """Minimal context manager returned by the proxy readiness socket fixture."""

    def __enter__(self) -> "_ReadyProxyConnection":
        """Enter one synthetic successful TCP connection."""

        return self

    def __exit__(self, exception_type: object, exception_value: object, traceback: object) -> bool:
        """Close one synthetic connection without suppressing exceptions."""

        return False


@pytest.fixture(autouse=True)
def _vpn_proxy_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make config-generation tests independent of a live Kubernetes Service."""

    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: _ReadyProxyConnection())


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
    output_dir = tmp_path / ".playwright-mcp" / "current"
    mcp_config_path = tmp_path / "mcp" / "config.json"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.42.0.8", port))],
    )
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        locale_config=BrowserLocaleConfig(locale="tr-TR"),
        mcp_config_path=mcp_config_path,
        output_dir=output_dir,
        persistent_profile_path=persistent_profile_path,
        port=12000,
        timezone="Europe/Istanbul",
        viewport_height=900,
        viewport_width=1440,
        vpn_proxy_server="vpn-egress:1080",
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
    assert config_payload["browser"]["launchOptions"]["proxy"] == {
        "bypass": "<-loopback>",
        "server": "socks5://10.42.0.8:1080",
    }
    assert "--disable-quic" in config_payload["browser"]["launchOptions"]["args"]
    assert all(
        not launch_arg.startswith("--host-resolver-rules=")
        for launch_arg in config_payload["browser"]["launchOptions"]["args"]
    )
    assert config_payload["browser"]["contextOptions"]["locale"] == "tr-TR"
    assert config_payload["browser"]["contextOptions"]["timezoneId"] == "Europe/Istanbul"
    assert config_payload["browser"]["contextOptions"]["viewport"] == {"height": 900, "width": 1440}
    assert config_payload["browser"]["userDataDir"] == str(persistent_profile_path)
    assert config_payload["sharedBrowserContext"] is True
    assert mcp_config_path.with_suffix(".stealth.js").is_file()
    assert (persistent_profile_path / "Preferences").read_text(encoding="utf-8") == "prefs"
    assert output_dir.is_dir()


def test_playwright_mcp_config_keeps_output_root_separate_from_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Write MCP file output under caller-owned root while runtime files stay scoped."""
    data_source_path = _runtime_data_source_create(tmp_path)
    output_dir = tmp_path / "output" / ".playwright-mcp" / "current"
    runtime_path = tmp_path / "runtime" / "browser_vpn_runtime"
    mcp_config_path = runtime_path / "playwright_mcp" / "config.json"
    persistent_profile_path = runtime_path / "playwright_profile"
    stealth_script_path = mcp_config_path.with_suffix(".stealth.js")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.42.0.8", port))],
    )
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        mcp_config_path=mcp_config_path,
        output_dir=output_dir,
        persistent_profile_path=persistent_profile_path,
        vpn_proxy_server="vpn-egress:1080",
    )

    playwright_mcp_command_argv_get(config)

    config_payload = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    assert config_payload["outputDir"] == str(output_dir)
    assert config_payload["browser"]["userDataDir"] == str(persistent_profile_path)
    assert config_payload["browser"]["initScript"] == [str(stealth_script_path)]
    assert mcp_config_path.is_file()
    assert stealth_script_path.is_file()
    assert mcp_config_path.parent == runtime_path / "playwright_mcp"
    assert stealth_script_path.parent == runtime_path / "playwright_mcp"
    assert output_dir not in mcp_config_path.parents
    assert output_dir not in persistent_profile_path.parents
    assert output_dir not in stealth_script_path.parents


def test_playwright_mcp_command_declares_allowed_hosts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Allow workflow containers to reach the MCP server through a runtime-owned service host."""
    data_source_path = _runtime_data_source_create(tmp_path)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.42.0.8", port))],
    )
    config = PlaywrightMcpConfig(
        allowed_host_list=["localhost", "127.0.0.1", "openvpn"],
        data_source_path=data_source_path,
        host="0.0.0.0",
        mcp_config_path=tmp_path / "mcp" / "config.json",
        output_dir=tmp_path / ".playwright-mcp" / "current",
        persistent_profile_path=tmp_path / "runtime-profile",
        vpn_proxy_server="vpn-egress:1080",
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
        "--vpn-proxy-server",
        "vpn-egress:1080",
    ]
    monkeypatch.setattr("sys.argv", argv)

    namespace = _args_parse()

    assert namespace.allowed_host_list == ["localhost", "127.0.0.1", "openvpn"]
    assert namespace.vpn_proxy_server == "vpn-egress:1080"
    assert "allowed_hosts" not in vars(namespace)


def test_playwright_mcp_command_uses_proxy_without_openvpn_data_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Build Playwright MCP argv when OpenVPN metadata remains gateway-only."""
    data_source_path = tmp_path / "data-source"
    data_source_path.mkdir()
    persistent_profile_path = tmp_path / "runtime-profile"
    output_dir = tmp_path / ".playwright-mcp" / "current"
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        mcp_config_path=tmp_path / "mcp" / "config.json",
        output_dir=output_dir,
        persistent_profile_path=persistent_profile_path,
        vpn_proxy_server="vpn-egress:1080",
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.42.0.8", port))],
    )

    command_argv = playwright_mcp_command_argv_get(config)

    assert command_argv[:3] == ["xvfb-run", "-a", "playwright-mcp"]
    assert output_dir.is_dir()


def test_playwright_mcp_rejects_output_dir_outside_artifact_namespace(tmp_path: Path) -> None:
    """Reject Playwright MCP output roots that would write files beside workflow artifacts."""
    data_source_path = tmp_path / "data-source"
    data_source_path.mkdir()

    with pytest.raises(ValueError, match="output_dir must be scoped under a .playwright-mcp directory"):
        PlaywrightMcpConfig(
            data_source_path=data_source_path,
            output_dir=tmp_path / "output",
            vpn_proxy_server="vpn-egress:1080",
        )


def test_browser_locale_config_uses_neutral_runtime_defaults(tmp_path: Path) -> None:
    """Keep caller-specific locale selection out of generic runtime defaults."""
    locale_config = BrowserLocaleConfig()
    mcp_config = PlaywrightMcpConfig(data_source_path=tmp_path / "data-source", vpn_proxy_server="vpn-egress:1080")

    assert locale_config.locale == "en-US"
    assert locale_config.navigator_language_list == ["en-US", "en"]
    assert mcp_config.locale_config == locale_config
    assert mcp_config.timezone == "UTC"


@pytest.mark.parametrize(
    ("locale", "expected_language_list", "expected_accept_language", "expected_profile_language"),
    [
        (
            "tr-TR",
            ["tr-TR", "tr", "en-US", "en"],
            "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "tr-TR,tr,en-US,en",
        ),
        (
            "de-DE",
            ["de-DE", "de", "en-US", "en"],
            "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "de-DE,de,en-US,en",
        ),
    ],
)
def test_browser_locale_config_derives_browser_language_contract(
    locale: str,
    expected_language_list: list[str],
    expected_accept_language: str,
    expected_profile_language: str,
) -> None:
    """Derive all browser language representations from one validated locale."""
    locale_config = BrowserLocaleConfig(locale=locale)

    assert locale_config.accept_language == expected_accept_language
    assert locale_config.navigator_language_list == expected_language_list
    assert locale_config.profile_language == expected_profile_language


def test_playwright_mcp_applies_one_locale_config_to_context_profile_and_stealth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Use one locale object for HTTP, profile, and navigator language configuration."""
    data_source_path = _runtime_data_source_create(tmp_path)
    persistent_profile_path = tmp_path / "runtime-profile"
    mcp_config_path = tmp_path / "runtime" / "playwright_mcp" / "config.json"
    captured_language_list: list[str] = []

    class FakeStealth:
        """Capture the language override supplied to the stealth script."""

        def __init__(self, *, navigator_languages_override: tuple[str, ...], navigator_platform_override: str) -> None:
            """Capture constructor input and expose a script payload."""
            captured_language_list.extend(navigator_languages_override)
            assert navigator_platform_override == "Linux x86_64"
            self.script_payload = "stealth"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.42.0.8", port))],
    )
    monkeypatch.setattr(playwright_mcp, "Stealth", FakeStealth)
    locale_config = BrowserLocaleConfig(locale="de-DE")
    config = PlaywrightMcpConfig(
        data_source_path=data_source_path,
        locale_config=locale_config,
        mcp_config_path=mcp_config_path,
        output_dir=tmp_path / ".playwright-mcp" / "current",
        persistent_profile_path=persistent_profile_path,
        timezone="Europe/Berlin",
        vpn_proxy_server="vpn-egress:1080",
    )

    playwright_mcp_command_argv_get(config)

    config_payload = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    preference_payload = json.loads((persistent_profile_path / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert config_payload["browser"]["contextOptions"]["extraHTTPHeaders"] == {
        "Accept-Language": locale_config.accept_language
    }
    assert config_payload["browser"]["contextOptions"]["locale"] == "de-DE"
    assert preference_payload["intl"] == {
        "accept_languages": locale_config.profile_language,
        "selected_languages": locale_config.profile_language,
    }
    assert captured_language_list == locale_config.navigator_language_list


@pytest.mark.parametrize(
    "vpn_proxy_server",
    ["http://vpn-egress:1080", "vpn-egress", "vpn-egress:0", "vpn egress:1080", "vpn@egress:1080"],
)
def test_playwright_mcp_rejects_non_endpoint_proxy_server(tmp_path: Path, vpn_proxy_server: str) -> None:
    """Require one strict host-and-port SOCKS endpoint."""
    with pytest.raises(ValueError, match="vpn_proxy_server"):
        PlaywrightMcpConfig(data_source_path=tmp_path / "data-source", vpn_proxy_server=vpn_proxy_server)
