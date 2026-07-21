"""Playwright MCP server launcher for browser/VPN runtime."""

import asyncio
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import time
from pathlib import Path
from typing import Self

from playwright_stealth import Stealth
from pydantic import BaseModel, ConfigDict, Field, model_validator

from browser_vpn_runtime.config import (
    DEFAULT_BROWSER_TIMEZONE,
    BrowserLocaleConfig,
)
from browser_vpn_runtime.playwright_profile import playwright_profile_materialize

DEFAULT_ACTION_TIMEOUT_MS = 30000
DEFAULT_ALLOWED_HOST_LIST = ["localhost", "127.0.0.1"]
DEFAULT_BROWSER_CHANNEL = "chrome"
DEFAULT_BACKEND_STOP_TIMEOUT_SECONDS = 10
DEFAULT_MCP_CONFIG_PATH = Path("/runtime/playwright_mcp/config.json")
DEFAULT_MCP_EXECUTABLE_NAME = "playwright-mcp"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_OUTPUT_DIR = Path("/runtime/.playwright-mcp/current")
DEFAULT_PORT = 8931
DEFAULT_PROXY_READY_TIMEOUT_SECONDS = 60
XVFB_RUN_EXECUTABLE_NAME = "xvfb-run"


class PlaywrightMcpError(RuntimeError):
    """Raised when Playwright MCP cannot be launched through the runtime boundary."""


class PlaywrightMcpConfig(BaseModel):
    """Validated Playwright MCP launch configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    action_timeout_ms: int = Field(default=DEFAULT_ACTION_TIMEOUT_MS, ge=1)
    allowed_host_list: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_HOST_LIST))
    browser_channel: str = DEFAULT_BROWSER_CHANNEL
    secret_root_path: Path
    host: str = DEFAULT_MCP_HOST
    isolated: bool = False
    locale_config: BrowserLocaleConfig = Field(default_factory=BrowserLocaleConfig)
    mcp_config_path: Path = DEFAULT_MCP_CONFIG_PATH
    navigation_timeout_ms: int = Field(default=60000, ge=1)
    output_dir: Path = DEFAULT_OUTPUT_DIR
    persistent_profile_path: Path | None = Path("/runtime/playwright_profile")
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    timezone: str = DEFAULT_BROWSER_TIMEZONE
    viewport_height: int = Field(default=1080, ge=1)
    viewport_width: int = Field(default=1920, ge=1)
    proxy_ready_timeout_seconds: int = Field(default=DEFAULT_PROXY_READY_TIMEOUT_SECONDS, ge=0)
    vpn_proxy_server: str = ""

    @model_validator(mode="after")
    def _playwright_mcp_launch_contract_validate(self) -> Self:
        """Validate output ownership and isolated profile semantics.

        Returns:
            Validated configuration.

        Raises:
            ValueError: If output_dir or the isolated profile combination is invalid.
        """

        if ".playwright-mcp" not in self.output_dir.parts:
            raise ValueError("output_dir must be scoped under a .playwright-mcp directory")
        if self.isolated and self.persistent_profile_path is not None:
            raise ValueError("isolated backend must omit persistent_profile_path")
        if not self.isolated and self.persistent_profile_path is None:
            raise ValueError("named backend requires persistent_profile_path")
        if self.vpn_proxy_server:
            _vpn_proxy_endpoint_get(self.vpn_proxy_server)
        return self


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
        "--disable-quic",
        "--disable-setuid-sandbox",
        "--no-sandbox",
    ]


def _mcp_config_payload_get(
    *,
    config: PlaywrightMcpConfig,
    persistent_profile_path: Path | None,
    stealth_script_path: Path,
    vpn_proxy_server: str,
) -> dict[str, object]:
    """Return Playwright MCP JSON config payload.

    Args:
        config: Validated launcher config.
        persistent_profile_path: Pod-local persistent profile path, or `None` for isolated sessions.
        stealth_script_path: JavaScript init script path generated from stealth.
        vpn_proxy_server: Literal resolved SOCKS5 proxy server endpoint, or an empty string for direct egress.

    Returns:
        JSON-serializable Playwright MCP config payload.
    """

    viewport_by_axis_map = {"height": config.viewport_height, "width": config.viewport_width}
    launch_option_json: dict[str, object] = {
        "args": _launch_arg_list_get(
            viewport_height=config.viewport_height,
            viewport_width=config.viewport_width,
        ),
        "channel": config.browser_channel,
        "chromiumSandbox": False,
        "headless": False,
    }
    if vpn_proxy_server:
        launch_option_json["proxy"] = {
            "bypass": "<-loopback>",
            "server": f"socks5://{vpn_proxy_server}",
        }
    browser_payload: dict[str, object] = {
        "browserName": "chromium",
        "contextOptions": {
            "deviceScaleFactor": 1,
            "extraHTTPHeaders": {
                "Accept-Language": config.locale_config.accept_language,
            },
            "locale": config.locale_config.locale,
            "screen": viewport_by_axis_map,
            "timezoneId": config.timezone,
            "viewport": viewport_by_axis_map,
        },
        "initScript": [str(stealth_script_path)],
        "isolated": config.isolated,
        "launchOptions": launch_option_json,
    }
    if persistent_profile_path is not None:
        browser_payload["userDataDir"] = str(persistent_profile_path)
    config_payload: dict[str, object] = {
        "browser": browser_payload,
        "imageResponses": "allow",
        "outputDir": str(config.output_dir),
        "outputMode": "file",
        "snapshot": {
            "mode": "full",
        },
        "timeouts": {
            "action": config.action_timeout_ms,
            "navigation": config.navigation_timeout_ms,
        },
    }
    if not config.isolated:
        config_payload["sharedBrowserContext"] = True
    return config_payload


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


def _profile_language_preference_sync(*, locale_config: BrowserLocaleConfig, profile_path: Path) -> None:
    """Write browser language preferences into the persistent profile.

    Args:
        locale_config: Locale and derived browser language settings.
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
    intl_by_name_map["accept_languages"] = locale_config.profile_language
    intl_by_name_map["selected_languages"] = locale_config.profile_language
    preferences_path.write_text(
        json.dumps(preference_by_name_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _stealth_script_write(*, locale_config: BrowserLocaleConfig, stealth_script_path: Path) -> None:
    """Write JavaScript init script generated by `playwright_stealth`.

    Args:
        locale_config: Locale and derived browser language settings.
        stealth_script_path: Target JavaScript init script path.
    """

    stealth_script_path.parent.mkdir(parents=True, exist_ok=True)
    stealth = Stealth(
        navigator_languages_override=tuple(locale_config.navigator_language_list),
        navigator_platform_override="Linux x86_64",
    )
    stealth_script_path.write_text(stealth.script_payload, encoding="utf-8")


def _vpn_proxy_endpoint_get(vpn_proxy_server: str) -> tuple[str, int]:
    """Parse one strict SOCKS5 gateway hostname and port endpoint.

    Args:
        vpn_proxy_server: Gateway endpoint in `hostname:port` form.

    Returns:
        Hostname and port for the gateway endpoint.

    Raises:
        ValueError: If the endpoint is not one hostname and TCP port.
    """

    hostname, separator, port_text = vpn_proxy_server.rpartition(":")
    if (
        not separator
        or not hostname
        or len(hostname) > 253
        or "/" in hostname
        or any(character.isspace() for character in hostname)
        or "://" in hostname
        or re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            hostname,
        )
        is None
        or not port_text.isdecimal()
    ):
        raise ValueError("vpn_proxy_server must be one hostname:port endpoint")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("vpn_proxy_server port must be between 1 and 65535")
    return hostname, port


def _vpn_proxy_server_resolve(vpn_proxy_server: str) -> tuple[str, int]:
    """Resolve the gateway hostname once into a literal IP endpoint.

    Args:
        vpn_proxy_server: Gateway endpoint in `hostname:port` form.

    Returns:
        Literal IP address and original port.

    Raises:
        PlaywrightMcpError: If hostname resolution returns no literal IP address.
    """

    hostname, port = _vpn_proxy_endpoint_get(vpn_proxy_server)
    try:
        address_info_list = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PlaywrightMcpError(f"vpn_proxy: cannot resolve {vpn_proxy_server}") from exc
    for address_info in address_info_list:
        address = address_info[4][0]
        try:
            literal_ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        return literal_ip.compressed, port
    raise PlaywrightMcpError(f"vpn_proxy: resolution returned no IP address for {vpn_proxy_server}")


def _vpn_proxy_server_literal_get(*, proxy_ip: str, proxy_port: int) -> str:
    """Return the SOCKS5 server endpoint formatted from a literal IP address.

    Args:
        proxy_ip: Literal IPv4 or IPv6 proxy address.
        proxy_port: SOCKS5 TCP port.

    Returns:
        Literal endpoint suitable for Playwright proxy configuration.
    """

    if ipaddress.ip_address(proxy_ip).version == 6:
        return f"[{proxy_ip}]:{proxy_port}"
    return f"{proxy_ip}:{proxy_port}"


def _vpn_proxy_wait(*, config: PlaywrightMcpConfig, proxy_ip: str, proxy_port: int) -> None:
    """Wait for the resolved SOCKS5 gateway endpoint to accept TCP connections.

    Args:
        config: Validated launcher configuration.
        proxy_ip: Literal resolved gateway IP address.
        proxy_port: SOCKS5 TCP port.

    Raises:
        PlaywrightMcpError: If the gateway does not become reachable before timeout.
    """

    deadline = time.monotonic() + config.proxy_ready_timeout_seconds
    while True:
        try:
            with socket.create_connection((proxy_ip, proxy_port), timeout=1):
                return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise PlaywrightMcpError(f"vpn_proxy: unavailable at {proxy_ip}:{proxy_port}") from exc
            time.sleep(1)


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
    """Build Playwright MCP argv after proxy reachability and profile preparation.

    Args:
        config: Validated Playwright MCP launch configuration.

    Returns:
        Command argv for the Playwright MCP process.

    Raises:
        PlaywrightMcpError: If the configured VPN proxy cannot be resolved or reached.
    """

    persistent_profile_path: Path | None = None
    if not config.isolated:
        if config.persistent_profile_path is None:
            raise PlaywrightMcpError("named Playwright MCP backend requires persistent_profile_path")
        playwright_profile_materialize(
            secret_root_path=config.secret_root_path,
            target_profile_path=config.persistent_profile_path,
        )
        persistent_profile_path = config.persistent_profile_path
    vpn_proxy_server = ""
    if config.vpn_proxy_server:
        vpn_proxy_ip, vpn_proxy_port = _vpn_proxy_server_resolve(config.vpn_proxy_server)
        vpn_proxy_server = _vpn_proxy_server_literal_get(proxy_ip=vpn_proxy_ip, proxy_port=vpn_proxy_port)
        _vpn_proxy_wait(config=config, proxy_ip=vpn_proxy_ip, proxy_port=vpn_proxy_port)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    if persistent_profile_path is not None:
        _profile_language_preference_sync(
            locale_config=config.locale_config,
            profile_path=persistent_profile_path,
        )
    stealth_script_path = config.mcp_config_path.with_suffix(".stealth.js")
    _stealth_script_write(locale_config=config.locale_config, stealth_script_path=stealth_script_path)
    _mcp_config_write(
        config_payload=_mcp_config_payload_get(
            config=config,
            persistent_profile_path=persistent_profile_path,
            stealth_script_path=stealth_script_path,
            vpn_proxy_server=vpn_proxy_server,
        ),
        config_path=config.mcp_config_path,
    )
    return _xvfb_command_argv_get(config)


class PlaywrightMcpBackend:
    """Own one lazily started internal Playwright MCP server process."""

    def __init__(self, config: PlaywrightMcpConfig) -> None:
        """Store the exact backend-local launch configuration.

        Args:
            config: Backend-local Playwright MCP configuration.
        """

        self.config = config
        self._process: asyncio.subprocess.Process | None = None

    @property
    def url(self) -> str:
        """Return the internal backend base URL.

        Returns:
            Internal loopback URL.
        """

        if self._process is None or self._process.returncode is not None:
            raise PlaywrightMcpError("Playwright MCP backend is not running")
        return f"http://{self.config.host}:{self.config.port}"

    async def start(self) -> None:
        """Start the backend process and wait until its TCP listener is ready."""

        if self._process is not None and self._process.returncode is None:
            return
        for executable_name in [XVFB_RUN_EXECUTABLE_NAME, DEFAULT_MCP_EXECUTABLE_NAME]:
            if shutil.which(executable_name) is None:
                raise PlaywrightMcpError(f"missing executable in PATH: {executable_name}")
        command_argv = await asyncio.to_thread(playwright_mcp_command_argv_get, self.config)
        environment = os.environ.copy()
        environment["TZ"] = self.config.timezone
        self._process = await asyncio.create_subprocess_exec(*command_argv, env=environment, start_new_session=True)
        try:
            await self._ready_wait()
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the backend and wait until the process has exited."""

        process = self._process
        if process is None:
            return
        self._process = None
        process_group_id = process.pid
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            if process.returncode is None:
                await process.wait()
            return
        try:
            await asyncio.wait_for(
                self._process_group_exit_wait(process=process, process_group_id=process_group_id),
                timeout=DEFAULT_BACKEND_STOP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.returncode is None:
                await process.wait()
            await asyncio.wait_for(
                self._process_group_absence_wait(process_group_id=process_group_id),
                timeout=DEFAULT_BACKEND_STOP_TIMEOUT_SECONDS,
            )

    @staticmethod
    async def _process_group_absence_wait(process_group_id: int) -> None:
        """Wait until the operating system reports no process-group members."""

        while True:
            try:
                os.killpg(process_group_id, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.05)

    async def _process_group_exit_wait(
        self,
        process: asyncio.subprocess.Process,
        process_group_id: int,
    ) -> None:
        """Wait for the wrapper and every process in its owned group to exit."""

        await process.wait()
        await self._process_group_absence_wait(process_group_id=process_group_id)

    async def _ready_wait(self) -> None:
        """Wait for the backend TCP listener or fail when the process exits."""

        if self._process is None:
            raise PlaywrightMcpError("Playwright MCP backend process was not started")
        deadline = asyncio.get_running_loop().time() + self.config.proxy_ready_timeout_seconds
        while True:
            if self._process.returncode is not None:
                raise PlaywrightMcpError(
                    f"Playwright MCP backend exited before readiness with code {self._process.returncode}"
                )
            try:
                _reader, writer = await asyncio.open_connection(self.config.host, self.config.port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError as exc:
                if asyncio.get_running_loop().time() >= deadline:
                    raise PlaywrightMcpError(
                        f"Playwright MCP backend did not listen on {self.config.host}:{self.config.port}"
                    ) from exc
                await asyncio.sleep(0.1)
