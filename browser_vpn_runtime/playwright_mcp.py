"""Playwright MCP server launcher for browser/VPN runtime."""

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Self

from playwright_stealth import Stealth
from pydantic import BaseModel, ConfigDict, Field, model_validator

from browser_vpn_runtime.config import BrowserRuntimeConfig
from browser_vpn_runtime.openvpn import openvpn_config_validate
from browser_vpn_runtime.runtime import BrowserRuntime

DEFAULT_ACCEPT_LANGUAGE = "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
DEFAULT_ACTION_TIMEOUT_MS = 30000
DEFAULT_ALLOWED_HOST_LIST = ["localhost", "127.0.0.1"]
DEFAULT_BROWSER_CHANNEL = "chrome"
DEFAULT_MCP_CONFIG_PATH = Path("/runtime/playwright_mcp/config.json")
DEFAULT_MCP_EXECUTABLE_NAME = "playwright-mcp"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_OUTPUT_DIR = Path("/runtime/.playwright-mcp/current")
DEFAULT_PORT = 8931
DEFAULT_VPN_READY_TIMEOUT_SECONDS = 60
XVFB_RUN_EXECUTABLE_NAME = "xvfb-run"


class PlaywrightMcpError(RuntimeError):
    """Raised when Playwright MCP cannot be launched through the runtime boundary."""


class PlaywrightMcpConfig(BaseModel):
    """Validated Playwright MCP launch configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    action_timeout_ms: int = Field(default=DEFAULT_ACTION_TIMEOUT_MS, ge=1)
    allowed_host_list: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_HOST_LIST))
    browser_channel: str = DEFAULT_BROWSER_CHANNEL
    data_source_path: Path
    host: str = DEFAULT_MCP_HOST
    locale: str = "tr-TR"
    mcp_config_path: Path = DEFAULT_MCP_CONFIG_PATH
    navigation_timeout_ms: int = Field(default=60000, ge=1)
    output_dir: Path = DEFAULT_OUTPUT_DIR
    persistent_profile_path: Path = Path("/runtime/playwright_profile")
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    require_openvpn: bool = False
    require_vpn_route: bool = False
    timezone: str = "Europe/Istanbul"
    viewport_height: int = Field(default=1080, ge=1)
    viewport_width: int = Field(default=1920, ge=1)
    vpn_ready_timeout_seconds: int = Field(default=DEFAULT_VPN_READY_TIMEOUT_SECONDS, ge=0)

    @model_validator(mode="after")
    def _playwright_mcp_output_dir_validate(self) -> Self:
        """Require a dedicated Playwright MCP artifact namespace.

        Returns:
            Validated configuration.

        Raises:
            ValueError: If output_dir is not scoped under `.playwright-mcp`.
        """

        if ".playwright-mcp" not in self.output_dir.parts:
            raise ValueError("output_dir must be scoped under a .playwright-mcp directory")
        return self


def _args_parse() -> argparse.Namespace:
    """Parse Playwright MCP launcher CLI arguments.

    Returns:
        Parsed CLI namespace.
    """

    parser = argparse.ArgumentParser(description="Launch Playwright MCP through browser-vpn-runtime.")
    parser.add_argument("--action-timeout-ms", default=DEFAULT_ACTION_TIMEOUT_MS, type=int)
    parser.add_argument(
        "--allowed-hosts",
        default=DEFAULT_ALLOWED_HOST_LIST,
        dest="allowed_host_list",
        type=_allowed_host_list_parse,
    )
    parser.add_argument("--browser-channel", default=DEFAULT_BROWSER_CHANNEL)
    parser.add_argument("--data-source-path", required=True, type=Path)
    parser.add_argument("--host", default=DEFAULT_MCP_HOST)
    parser.add_argument("--locale", default="tr-TR")
    parser.add_argument("--mcp-config-path", default=DEFAULT_MCP_CONFIG_PATH, type=Path)
    parser.add_argument("--navigation-timeout-ms", default=60000, type=int)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    parser.add_argument("--persistent-profile-path", default=Path("/runtime/playwright_profile"), type=Path)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--require-openvpn", action="store_true")
    parser.add_argument("--require-vpn-route", action="store_true")
    parser.add_argument("--timezone", default="Europe/Istanbul")
    parser.add_argument("--viewport-height", default=1080, type=int)
    parser.add_argument("--viewport-width", default=1920, type=int)
    parser.add_argument("--vpn-ready-timeout-seconds", default=DEFAULT_VPN_READY_TIMEOUT_SECONDS, type=int)
    return parser.parse_args()


def _allowed_host_list_parse(value: str) -> list[str]:
    """Parse a comma-separated Playwright MCP allowed-host list.

    Args:
        value: Comma-separated host list.

    Returns:
        Non-empty host list.

    Raises:
        argparse.ArgumentTypeError: If the list is empty.
    """

    allowed_host_list = [host.strip() for host in value.split(",") if host.strip()]
    if not allowed_host_list:
        raise argparse.ArgumentTypeError("allowed-hosts must contain at least one host")
    return allowed_host_list


def _allowed_host_list_with_port_get(*, allowed_host_list: list[str], port: int) -> list[str]:
    """Return MCP allowed hosts including exact Host header values with port.

    Args:
        allowed_host_list: Configured host allow-list.
        port: MCP HTTP port.

    Returns:
        Host allow-list expanded with `host:port` forms.
    """

    expanded_allowed_host_list: list[str] = []
    for allowed_host in allowed_host_list:
        expanded_allowed_host_list.append(allowed_host)
        if allowed_host != "*" and ":" not in allowed_host:
            expanded_allowed_host_list.append(f"{allowed_host}:{port}")
    return expanded_allowed_host_list


def _launch_arg_list_get(*, viewport_height: int, viewport_width: int) -> list[str]:
    """Return Chromium launch arguments for the MCP browser.

    Args:
        viewport_height: Browser viewport height.
        viewport_width: Browser viewport width.

    Returns:
        Chromium launch argument list.
    """

    return [
        f"--window-size={viewport_width},{viewport_height}",
        "--start-maximized",
        "--window-position=0,0",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=AutomationControlled",
        "--disable-setuid-sandbox",
        "--no-sandbox",
    ]


def _mcp_config_payload_get(
    *,
    config: PlaywrightMcpConfig,
    persistent_profile_path: Path,
    stealth_script_path: Path,
) -> dict[str, object]:
    """Return Playwright MCP JSON config payload.

    Args:
        config: Validated launcher config.
        persistent_profile_path: Pod-local persistent profile path.
        stealth_script_path: JavaScript init script path generated from stealth.

    Returns:
        JSON-serializable Playwright MCP config payload.
    """

    viewport_by_axis_map = {"height": config.viewport_height, "width": config.viewport_width}
    return {
        "browser": {
            "browserName": "chromium",
            "contextOptions": {
                "deviceScaleFactor": 1,
                "extraHTTPHeaders": {
                    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
                },
                "locale": config.locale,
                "screen": viewport_by_axis_map,
                "timezoneId": config.timezone,
                "viewport": viewport_by_axis_map,
            },
            "initScript": [str(stealth_script_path)],
            "launchOptions": {
                "args": _launch_arg_list_get(
                    viewport_height=config.viewport_height,
                    viewport_width=config.viewport_width,
                ),
                "channel": config.browser_channel,
                "chromiumSandbox": False,
                "headless": False,
            },
            "userDataDir": str(persistent_profile_path),
        },
        "imageResponses": "allow",
        "outputDir": str(config.output_dir),
        "outputMode": "file",
        "sharedBrowserContext": True,
        "snapshot": {
            "mode": "full",
        },
        "timeouts": {
            "action": config.action_timeout_ms,
            "navigation": config.navigation_timeout_ms,
        },
    }


def _mcp_config_write(*, config_payload: dict[str, object], config_path: Path) -> None:
    """Write Playwright MCP config JSON.

    Args:
        config_payload: JSON-serializable config payload.
        config_path: Target config path.
    """

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _profile_language_preference_sync(*, profile_path: Path) -> None:
    """Write browser language preferences into the persistent profile.

    Args:
        profile_path: Persistent profile path used as MCP `userDataDir`.
    """

    preferences_path = profile_path / "Default" / "Preferences"
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    preference_by_name_map: dict[str, object] = {}
    if preferences_path.exists():
        try:
            preference_by_name_map = json.loads(preferences_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            preference_by_name_map = {}
    preference_by_name_map.setdefault("intl", {})
    intl_by_name_map = preference_by_name_map["intl"]
    if not isinstance(intl_by_name_map, dict):
        intl_by_name_map = {}
        preference_by_name_map["intl"] = intl_by_name_map
    intl_by_name_map["accept_languages"] = DEFAULT_ACCEPT_LANGUAGE
    intl_by_name_map["selected_languages"] = DEFAULT_ACCEPT_LANGUAGE
    preferences_path.write_text(
        json.dumps(preference_by_name_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _runtime_wait(*, config: PlaywrightMcpConfig, runtime: BrowserRuntime) -> None:
    """Wait until required browser/VPN runtime prerequisites are ready.

    Args:
        config: Validated launcher config.
        runtime: Runtime readiness boundary.

    Raises:
        PlaywrightMcpError: If runtime prerequisites do not become ready.
    """

    deadline = time.monotonic() + config.vpn_ready_timeout_seconds
    while True:
        runtime_state = runtime.readiness_check()
        if runtime_state.is_ready:
            return
        non_retry_problem_list = [
            problem for problem in runtime_state.problem_list if not problem.startswith("vpn_route:")
        ]
        if non_retry_problem_list or time.monotonic() >= deadline:
            raise PlaywrightMcpError("; ".join(runtime_state.problem_list))
        time.sleep(1)


def _stealth_script_write(*, stealth_script_path: Path) -> None:
    """Write JavaScript init script generated by `playwright_stealth`.

    Args:
        stealth_script_path: Target JavaScript init script path.
    """

    stealth_script_path.parent.mkdir(parents=True, exist_ok=True)
    stealth = Stealth(
        navigator_languages_override=("tr-TR", "tr", "en-US", "en"),
        navigator_platform_override="Linux x86_64",
    )
    stealth_script_path.write_text(stealth.script_payload, encoding="utf-8")


def _validated_openvpn_config_name_get(config: PlaywrightMcpConfig) -> str:
    """Return validated OpenVPN config name selected by the DataSource.

    Args:
        config: Validated launcher config.

    Returns:
        OpenVPN config file name, or empty string when OpenVPN is not configured.

    Raises:
        PlaywrightMcpError: If OpenVPN is required but missing.
    """

    if not (config.data_source_path / "openvpn" / "config.json").exists():
        if config.require_openvpn:
            raise PlaywrightMcpError("openvpn_config: missing required OpenVPN metadata file")
        return ""
    return openvpn_config_validate(config.data_source_path).openvpn_config_name


def _xvfb_command_argv_get(config: PlaywrightMcpConfig) -> list[str]:
    """Return the final headed Playwright MCP process argv.

    Args:
        config: Validated launcher config.

    Returns:
        Process argv for `os.execvp`.
    """

    return [
        XVFB_RUN_EXECUTABLE_NAME,
        "-a",
        DEFAULT_MCP_EXECUTABLE_NAME,
        "--allowed-hosts",
        ",".join(_allowed_host_list_with_port_get(allowed_host_list=config.allowed_host_list, port=config.port)),
        "--config",
        str(config.mcp_config_path),
        "--host",
        config.host,
        "--port",
        str(config.port),
    ]


def playwright_mcp_command_argv_get(config: PlaywrightMcpConfig) -> list[str]:
    """Build Playwright MCP argv after strict runtime readiness validation.

    Args:
        config: Validated Playwright MCP launch configuration.

    Returns:
        Command argv for the Playwright MCP process.

    Raises:
        PlaywrightMcpError: If the browser/VPN runtime is not ready.
    """

    openvpn_config_name = _validated_openvpn_config_name_get(config)
    runtime_config = BrowserRuntimeConfig(
        data_source_path=config.data_source_path,
        locale=config.locale,
        openvpn_config_name=openvpn_config_name,
        persistent_profile_path=config.persistent_profile_path,
        require_vpn_route=config.require_vpn_route or bool(openvpn_config_name),
        timezone=config.timezone,
        viewport_height=config.viewport_height,
        viewport_width=config.viewport_width,
    )
    runtime = BrowserRuntime(runtime_config)
    _runtime_wait(config=config, runtime=runtime)
    runtime_context = runtime.playwright_runtime_context_get()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    _profile_language_preference_sync(profile_path=runtime_context.persistent_profile_path)
    stealth_script_path = config.mcp_config_path.with_suffix(".stealth.js")
    _stealth_script_write(stealth_script_path=stealth_script_path)
    _mcp_config_write(
        config_payload=_mcp_config_payload_get(
            config=config,
            persistent_profile_path=runtime_context.persistent_profile_path,
            stealth_script_path=stealth_script_path,
        ),
        config_path=config.mcp_config_path,
    )
    return _xvfb_command_argv_get(config)


def main() -> None:
    """Replace the current process with the runtime-owned Playwright MCP server.

    Raises:
        PlaywrightMcpError: If required executables are missing.
    """

    config = PlaywrightMcpConfig(**vars(_args_parse()))
    for executable_name in [XVFB_RUN_EXECUTABLE_NAME, DEFAULT_MCP_EXECUTABLE_NAME]:
        if shutil.which(executable_name) is None:
            raise PlaywrightMcpError(f"missing executable in PATH: {executable_name}")
    command_argv = playwright_mcp_command_argv_get(config)
    os.environ["TZ"] = config.timezone
    os.execvp(command_argv[0], command_argv)


if __name__ == "__main__":
    main()
