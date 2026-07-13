"""Run-local profile router for internal Playwright MCP backend processes."""

import argparse
import asyncio
import json
import re
import socket
from collections.abc import Callable
from contextlib import AsyncExitStack
from itertools import combinations
from pathlib import Path
from typing import Protocol, Self

from aiohttp import ClientResponse, ClientSession, ClientTimeout, web
from multidict import CIMultiDict, MultiDict, MultiMapping
from pydantic import BaseModel, ConfigDict, Field, model_validator

from browser_vpn_runtime.config import DEFAULT_BROWSER_LOCALE, DEFAULT_BROWSER_TIMEZONE, BrowserLocaleConfig
from browser_vpn_runtime.playwright_mcp import (
    DEFAULT_ACTION_TIMEOUT_MS,
    DEFAULT_ALLOWED_HOST_LIST,
    DEFAULT_BROWSER_CHANNEL,
    DEFAULT_MCP_HOST,
    DEFAULT_PORT,
    DEFAULT_PROXY_READY_TIMEOUT_SECONDS,
    PlaywrightMcpBackend,
    PlaywrightMcpConfig,
    _allowed_host_list_with_port_get,
)
from browser_vpn_runtime.playwright_profile import playwright_profile_replace, playwright_profile_snapshot

DEFAULT_CANDIDATE_ROOT_PATH = Path("/runtime/mcp_playwright_profile/writeback_candidate")
DEFAULT_MCP_BACKEND_RUNTIME_ROOT_PATH = Path("/runtime/playwright_mcp_backend")
DEFAULT_MCP_OUTPUT_ROOT_PATH = Path("/output/.playwright-mcp")
DEFAULT_PROFILE_ROOT_PATH = Path("/runtime/mcp_playwright_profile/profile")
HOP_BY_HOP_HEADER_NAME_SET = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?")
ROUTER_QUERY_NAME_SET = {"profile", "profile_source"}
WRITEBACK_CANDIDATE_PATH = "/runtime/mcp-playwright-profile/writeback-candidate"


class PlaywrightMcpBackendProtocol(Protocol):
    """Lifecycle surface required from one internal MCP backend."""

    @property
    def url(self) -> str:
        """Return the active backend URL."""

    async def start(self) -> None:
        """Start the backend when it is not active."""

    async def stop(self) -> None:
        """Stop the backend and wait for process exit."""


class PlaywrightMcpRoute(BaseModel):
    """Validated profile selection for one proxied MCP request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    physical_profile: str | None
    source_physical_profile: str | None


class PlaywrightMcpRouterConfig(BaseModel):
    """Validated paths and backend template owned by the public router."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    backend_config: PlaywrightMcpConfig
    backend_runtime_root_path: Path = DEFAULT_MCP_BACKEND_RUNTIME_ROOT_PATH
    candidate_root_path: Path = DEFAULT_CANDIDATE_ROOT_PATH
    host: str = "0.0.0.0"
    output_root_path: Path = DEFAULT_MCP_OUTPUT_ROOT_PATH
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    profile_root_path: Path = DEFAULT_PROFILE_ROOT_PATH

    @model_validator(mode="after")
    def _path_separation_validate(self) -> Self:
        """Require disjoint backend-local owner roots.

        Returns:
            Validated router configuration.

        Raises:
            ValueError: If two mutable owner roots are identical.
        """

        root_path_list = [
            self.backend_runtime_root_path,
            self.candidate_root_path,
            self.output_root_path,
            self.profile_root_path,
        ]
        for first_path, second_path in combinations(root_path_list, 2):
            if (
                first_path == second_path
                or first_path.is_relative_to(second_path)
                or second_path.is_relative_to(first_path)
            ):
                raise ValueError("router runtime, candidate, output, and profile roots must be disjoint")
        return self


class McpPlaywrightProfileRouter:
    """Route public MCP traffic to run-local profile-specific backends."""

    def __init__(
        self,
        config: PlaywrightMcpRouterConfig,
        backend_factory: Callable[[PlaywrightMcpConfig], PlaywrightMcpBackendProtocol] = PlaywrightMcpBackend,
    ) -> None:
        """Store configuration and lazy backend factory.

        Args:
            config: Router filesystem and backend template configuration.
            backend_factory: Callable creating one backend lifecycle owner.
        """

        self.config = config
        self._backend_by_profile_map: dict[str | None, PlaywrightMcpBackendProtocol] = {}
        self._backend_factory = backend_factory
        self._backend_port_set: set[int] = set()
        self._candidate_lock = asyncio.Lock()
        self._client_session: ClientSession | None = None
        self._lock_by_profile_map: dict[str, asyncio.Lock] = {}
        self._unprofiled_lock = asyncio.Lock()

    async def close(self) -> None:
        """Close all active internal backends and the proxy client session."""

        for backend in self._backend_by_profile_map.values():
            await backend.stop()
        if self._client_session is not None:
            await self._client_session.close()
            self._client_session = None

    async def request_proxy(self, request: web.Request) -> web.StreamResponse:
        """Proxy one MCP request through the backend selected by the route query."""

        try:
            self._host_validate(request)
            route = self._route_from_request(request)
            request_body = await request.read()
            if (
                route.source_physical_profile is not None
                and not (self.config.profile_root_path / route.source_physical_profile).is_dir()
            ):
                raise FileNotFoundError(f"source profile is missing: {route.source_physical_profile}")
            source_reset_need = self._source_reset_need(request=request, request_body=request_body, route=route)
            if route.physical_profile is None:
                async with self._unprofiled_lock:
                    backend = await self._backend_get_start(physical_profile=None)
                    upstream_response = await self._backend_request_get(
                        backend=backend,
                        request=request,
                        request_body=request_body,
                    )
                return await self._backend_response_proxy(request=request, upstream_response=upstream_response)
            lock_name_list = [route.physical_profile]
            if source_reset_need:
                if route.source_physical_profile is None:
                    raise RuntimeError("source reset requires source_physical_profile")
                lock_name_list.append(route.source_physical_profile)
            async with AsyncExitStack() as stack:
                for physical_profile in sorted(lock_name_list):
                    await stack.enter_async_context(self._profile_lock_get(physical_profile))
                if source_reset_need:
                    await self._profile_source_apply(route)
                elif not (self.config.profile_root_path / route.physical_profile).exists():
                    await asyncio.to_thread(
                        playwright_profile_replace,
                        source_profile_path=self.config.backend_config.data_source_path / "playwright_profile",
                        target_profile_path=self.config.profile_root_path / route.physical_profile,
                    )
                backend = await self._backend_get_start(physical_profile=route.physical_profile)
                upstream_response = await self._backend_request_get(
                    backend=backend,
                    request=request,
                    request_body=request_body,
                )
            return await self._backend_response_proxy(request=request, upstream_response=upstream_response)
        except (FileNotFoundError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

    async def writeback_candidate_publish(self, request: web.Request) -> web.Response:
        """Stop one named backend and atomically publish its current profile."""

        try:
            self._host_validate(request)
            if await request.read():
                raise ValueError("writeback candidate request body must be empty")
            physical_profile = self._physical_profile_from_request(request)
            async with self._profile_lock_get(physical_profile):
                async with self._candidate_lock:
                    await self._profile_candidate_publish(physical_profile=physical_profile)
            return web.Response(status=204)
        except (FileNotFoundError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

    async def _backend_request_get(
        self,
        backend: PlaywrightMcpBackendProtocol,
        request: web.Request,
        request_body: bytes,
    ) -> ClientResponse:
        """Deliver one request and return the upstream response after headers arrive."""

        client_session = self._client_session_get()
        query = MultiDict((name, value) for name, value in request.query.items() if name not in ROUTER_QUERY_NAME_SET)
        request_headers = self._end_to_end_headers_get(request.headers)
        for name in ["content-length", "host"]:
            request_headers.popall(name, None)
        return await client_session.request(
            method=request.method,
            url=f"{backend.url}{request.rel_url.raw_path}",
            params=query,
            headers=request_headers,
            data=request_body,
            allow_redirects=False,
        )

    async def _backend_response_proxy(
        self,
        request: web.Request,
        upstream_response: ClientResponse,
    ) -> web.StreamResponse:
        """Stream one delivered upstream response to the public MCP client."""

        response_headers = self._end_to_end_headers_get(upstream_response.headers)
        response = web.StreamResponse(
            status=upstream_response.status, reason=upstream_response.reason, headers=response_headers
        )
        await response.prepare(request)
        try:
            async for chunk in upstream_response.content.iter_chunked(64 * 1024):
                await response.write(chunk)
        finally:
            upstream_response.release()
        await response.write_eof()
        return response

    async def _backend_get_start(self, physical_profile: str | None) -> PlaywrightMcpBackendProtocol:
        """Create one backend lifecycle owner when absent and ensure it is running."""

        backend = self._backend_by_profile_map.get(physical_profile)
        if backend is None:
            backend = self._backend_factory(self._backend_config_get(physical_profile))
            self._backend_by_profile_map[physical_profile] = backend
        await backend.start()
        return backend

    def _backend_config_get(self, physical_profile: str | None) -> PlaywrightMcpConfig:
        """Build one backend-local config with disjoint paths and an internal port."""

        backend_relative_path = Path("isolated") if physical_profile is None else Path("named") / physical_profile
        backend_runtime_path = self.config.backend_runtime_root_path / backend_relative_path
        persistent_profile_path = None if physical_profile is None else self.config.profile_root_path / physical_profile
        port = _loopback_port_get()
        while port in self._backend_port_set:
            port = _loopback_port_get()
        config_payload = self.config.backend_config.model_dump(mode="python")
        config_payload.update(
            {
                "allowed_host_list": list(
                    dict.fromkeys([*self.config.backend_config.allowed_host_list, *DEFAULT_ALLOWED_HOST_LIST])
                ),
                "host": DEFAULT_MCP_HOST,
                "isolated": physical_profile is None,
                "mcp_config_path": backend_runtime_path / "config.json",
                "output_dir": self.config.output_root_path / backend_relative_path,
                "persistent_profile_path": persistent_profile_path,
                "port": port,
            }
        )
        backend_config = PlaywrightMcpConfig(**config_payload)
        self._backend_port_set.add(backend_config.port)
        return backend_config

    def _client_session_get(self) -> ClientSession:
        """Return the router-owned no-decompression proxy session."""

        if self._client_session is None:
            self._client_session = ClientSession(auto_decompress=False, timeout=ClientTimeout(total=None))
        return self._client_session

    @staticmethod
    def _end_to_end_headers_get(headers: MultiMapping[str]) -> CIMultiDict[str]:
        """Copy headers without fixed or Connection-declared hop-by-hop fields."""

        end_to_end_headers = CIMultiDict(headers)
        connection_header_list = end_to_end_headers.getall("connection", [])
        connection_option_set = {
            option.strip().lower()
            for connection_header in connection_header_list
            for option in connection_header.split(",")
            if option.strip()
        }
        for name in HOP_BY_HOP_HEADER_NAME_SET | connection_option_set:
            end_to_end_headers.popall(name, None)
        return end_to_end_headers

    async def _profile_candidate_publish(self, physical_profile: str) -> None:
        """Stop one backend and atomically snapshot its current profile."""

        backend = self._backend_by_profile_map.get(physical_profile)
        if backend is not None:
            await backend.stop()
        await asyncio.to_thread(
            playwright_profile_snapshot,
            runtime_profile_path=self.config.profile_root_path / physical_profile,
            writeback_candidate_path=self.config.candidate_root_path,
        )

    def _profile_lock_get(self, physical_profile: str) -> asyncio.Lock:
        """Return the stable lock for one physical profile."""

        lock = self._lock_by_profile_map.get(physical_profile)
        if lock is None:
            lock = asyncio.Lock()
            self._lock_by_profile_map[physical_profile] = lock
        return lock

    async def _profile_source_apply(self, route: PlaywrightMcpRoute) -> None:
        """Stop the target backend and atomically reset it from the explicit source."""

        if route.physical_profile is None or route.source_physical_profile is None:
            raise RuntimeError("profile source reset requires named source and target")
        source_profile_path = self.config.profile_root_path / route.source_physical_profile
        if not source_profile_path.is_dir():
            raise FileNotFoundError(f"source profile is missing: {route.source_physical_profile}")
        backend = self._backend_by_profile_map.get(route.physical_profile)
        if backend is not None:
            await backend.stop()
        await asyncio.to_thread(
            playwright_profile_replace,
            source_profile_path=source_profile_path,
            target_profile_path=self.config.profile_root_path / route.physical_profile,
        )

    def _physical_profile_from_request(self, request: web.Request) -> str:
        """Parse the exact writeback candidate profile query."""

        unknown_name_set = set(request.query) - {"profile"}
        if unknown_name_set:
            raise ValueError(f"unexpected query parameter: {sorted(unknown_name_set)[0]}")
        physical_profile = self._query_value_get(request=request, name="profile")
        if physical_profile is None:
            raise ValueError("profile is required")
        return self._profile_name_validate(name="profile", value=physical_profile)

    def _host_validate(self, request: web.Request) -> None:
        """Reject a public Host header outside the configured MCP allow-list."""

        allowed_host_list = self.config.backend_config.allowed_host_list
        if "*" in allowed_host_list:
            return
        allowed_host_set = {
            allowed_host.lower()
            for allowed_host in _allowed_host_list_with_port_get(
                allowed_host_list=allowed_host_list,
                port=self.config.port,
            )
        }
        if request.headers.get("host", "").lower() not in allowed_host_set:
            raise ValueError("request Host is not allowed")

    def _route_from_request(self, request: web.Request) -> PlaywrightMcpRoute:
        """Parse one public route without losing duplicate query values."""

        physical_profile = self._query_value_get(request=request, name="profile")
        source_physical_profile = self._query_value_get(request=request, name="profile_source")
        if physical_profile is None and source_physical_profile is not None:
            raise ValueError("profile_source requires profile")
        if physical_profile is not None:
            physical_profile = self._profile_name_validate(name="profile", value=physical_profile)
        if source_physical_profile is not None:
            source_physical_profile = self._profile_name_validate(name="profile_source", value=source_physical_profile)
        if physical_profile is not None and physical_profile == source_physical_profile:
            raise ValueError("profile_source must differ from profile")
        return PlaywrightMcpRoute(
            physical_profile=physical_profile,
            source_physical_profile=source_physical_profile,
        )

    @staticmethod
    def _profile_name_validate(name: str, value: str) -> str:
        """Reject a physical profile name that is unsafe for one path segment."""

        if PROFILE_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{name} is unsafe")
        return value

    @staticmethod
    def _query_value_get(request: web.Request, name: str) -> str | None:
        """Return one optional query value while rejecting duplicates."""

        value_list = request.query.getall(name, [])
        if len(value_list) > 1:
            raise ValueError(f"{name} must occur at most once")
        return value_list[0] if value_list else None

    @staticmethod
    def _source_reset_need(request: web.Request, request_body: bytes, route: PlaywrightMcpRoute) -> bool:
        """Return whether this request starts a new explicit-source MCP session."""

        if route.source_physical_profile is None or request.method != "POST" or "mcp-session-id" in request.headers:
            return False
        try:
            request_payload = json.loads(request_body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return False
        return isinstance(request_payload, dict) and request_payload.get("method") == "initialize"


def _args_parse() -> argparse.Namespace:
    """Parse the public Playwright MCP profile router CLI."""

    parser = argparse.ArgumentParser(description="Route Playwright MCP requests by run-local physical profile.")
    parser.add_argument("--action-timeout-ms", default=DEFAULT_ACTION_TIMEOUT_MS, type=int)
    parser.add_argument(
        "--allowed-hosts", default=DEFAULT_ALLOWED_HOST_LIST, dest="allowed_host_list", type=_allowed_host_list_parse
    )
    parser.add_argument("--backend-runtime-root-path", default=DEFAULT_MCP_BACKEND_RUNTIME_ROOT_PATH, type=Path)
    parser.add_argument("--browser-channel", default=DEFAULT_BROWSER_CHANNEL)
    parser.add_argument("--candidate-root-path", default=DEFAULT_CANDIDATE_ROOT_PATH, type=Path)
    parser.add_argument("--data-source-path", required=True, type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--locale", default=DEFAULT_BROWSER_LOCALE)
    parser.add_argument("--navigation-timeout-ms", default=60000, type=int)
    parser.add_argument("--output-root-path", default=DEFAULT_MCP_OUTPUT_ROOT_PATH, type=Path)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--profile-root-path", default=DEFAULT_PROFILE_ROOT_PATH, type=Path)
    parser.add_argument("--proxy-ready-timeout-seconds", default=DEFAULT_PROXY_READY_TIMEOUT_SECONDS, type=int)
    parser.add_argument("--timezone", default=DEFAULT_BROWSER_TIMEZONE)
    parser.add_argument("--viewport-height", default=1080, type=int)
    parser.add_argument("--viewport-width", default=1920, type=int)
    parser.add_argument("--vpn-proxy-server", required=True)
    return parser.parse_args()


def _allowed_host_list_parse(value: str) -> list[str]:
    """Parse a non-empty comma-separated backend allowed-host list."""

    allowed_host_list = [host.strip() for host in value.split(",") if host.strip()]
    if not allowed_host_list:
        raise argparse.ArgumentTypeError("allowed-hosts must contain at least one host")
    return allowed_host_list


def _loopback_port_get() -> int:
    """Reserve and release one currently available loopback TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind((DEFAULT_MCP_HOST, 0))
        return int(server_socket.getsockname()[1])


def main() -> None:
    """Run the public aiohttp profile router until the process is stopped."""

    argument_by_name_map = vars(_args_parse())
    host = argument_by_name_map.pop("host")
    port = argument_by_name_map.pop("port")
    locale = argument_by_name_map.pop("locale")
    backend_runtime_root_path = argument_by_name_map.pop("backend_runtime_root_path")
    candidate_root_path = argument_by_name_map.pop("candidate_root_path")
    output_root_path = argument_by_name_map.pop("output_root_path")
    profile_root_path = argument_by_name_map.pop("profile_root_path")
    backend_config = PlaywrightMcpConfig(
        host=DEFAULT_MCP_HOST,
        locale_config=BrowserLocaleConfig(locale=locale),
        mcp_config_path=backend_runtime_root_path / "base" / "config.json",
        output_dir=output_root_path / "base",
        persistent_profile_path=profile_root_path / "_template",
        port=DEFAULT_PORT,
        **argument_by_name_map,
    )
    router_config = PlaywrightMcpRouterConfig(
        backend_config=backend_config,
        backend_runtime_root_path=backend_runtime_root_path,
        candidate_root_path=candidate_root_path,
        host=host,
        output_root_path=output_root_path,
        port=port,
        profile_root_path=profile_root_path,
    )
    router = McpPlaywrightProfileRouter(config=router_config)
    application = web.Application()
    application.router.add_post(WRITEBACK_CANDIDATE_PATH, router.writeback_candidate_publish)
    application.router.add_route("*", "/{path:.*}", router.request_proxy)
    application.on_cleanup.append(lambda application: router.close())
    web.run_app(application, host=router_config.host, port=router_config.port)


if __name__ == "__main__":
    main()
