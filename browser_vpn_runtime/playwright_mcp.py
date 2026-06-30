"""Playwright MCP server launcher for browser/VPN runtime."""

import argparse
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from browser_vpn_runtime.config import BrowserRuntimeConfig
from browser_vpn_runtime.openvpn import openvpn_config_validate
from browser_vpn_runtime.runtime import BrowserRuntime

DEFAULT_MCP_PACKAGE_NAME = "@playwright/mcp@latest"
DEFAULT_OUTPUT_DIR = Path("/runtime/playwright_mcp")


class PlaywrightMcpError(RuntimeError):
    """Raised when Playwright MCP cannot be launched through the runtime boundary."""


class PlaywrightMcpConfig(BaseModel):
    """Validated Playwright MCP launch configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    data_source_path: Path
    headless: bool = True
    locale: str = "en-US"
    mcp_package_name: str = DEFAULT_MCP_PACKAGE_NAME
    output_dir: Path = DEFAULT_OUTPUT_DIR
    persistent_profile_path: Path = Path("/runtime/playwright_profile")
    require_vpn_route: bool = False
    timezone: str = "UTC"
    viewport_height: int = Field(default=720, ge=1)
    viewport_width: int = Field(default=1280, ge=1)


def args_parse() -> argparse.Namespace:
    """Parse Playwright MCP launcher CLI arguments.

    Returns:
        Parsed CLI namespace.
    """

    parser = argparse.ArgumentParser(description="Launch Playwright MCP through browser-vpn-runtime.")
    parser.add_argument("--data-source-path", type=Path, required=True)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--mcp-package-name", default=DEFAULT_MCP_PACKAGE_NAME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--persistent-profile-path", type=Path, default=Path("/runtime/playwright_profile"))
    parser.add_argument("--require-vpn-route", action="store_true")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--viewport-height", type=int, default=720)
    parser.add_argument("--viewport-width", type=int, default=1280)
    return parser.parse_args()


def playwright_mcp_command_argv_get(config: PlaywrightMcpConfig) -> list[str]:
    """Build Playwright MCP argv after strict runtime readiness validation.

    Args:
        config: Validated Playwright MCP launch configuration.

    Returns:
        Command argv for the Playwright MCP process.

    Raises:
        PlaywrightMcpError: If the browser/VPN runtime is not ready.
    """

    openvpn_config_name = ""
    if (config.data_source_path / "openvpn" / "config.json").exists():
        openvpn_config_state = openvpn_config_validate(config.data_source_path)
        openvpn_config_name = openvpn_config_state.openvpn_config_name
    runtime_config = BrowserRuntimeConfig(
        data_source_path=config.data_source_path,
        locale=config.locale,
        openvpn_config_name=openvpn_config_name,
        persistent_profile_path=config.persistent_profile_path,
        require_vpn_route=config.require_vpn_route,
        timezone=config.timezone,
        viewport_height=config.viewport_height,
        viewport_width=config.viewport_width,
    )
    runtime = BrowserRuntime(runtime_config)
    runtime_state = runtime.readiness_check()
    if not runtime_state.is_ready:
        raise PlaywrightMcpError("; ".join(runtime_state.problem_list))
    runtime_context = runtime.playwright_runtime_context_get()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    command_argv = [
        "npx",
        "--yes",
        config.mcp_package_name,
        "--browser",
        "chrome",
        "--no-sandbox",
        "--user-data-dir",
        str(runtime_context.persistent_profile_path),
        "--viewport-size",
        f"{runtime_context.viewport_width}x{runtime_context.viewport_height}",
        "--output-dir",
        str(config.output_dir),
        "--output-mode",
        "file",
    ]
    if config.headless:
        command_argv.append("--headless")
    return command_argv


def main() -> None:
    """Replace the current process with the runtime-owned Playwright MCP server."""

    config = PlaywrightMcpConfig(**vars(args_parse()))
    command_argv = playwright_mcp_command_argv_get(config)
    os.environ["TZ"] = config.timezone
    os.execvp(command_argv[0], command_argv)


if __name__ == "__main__":
    main()
