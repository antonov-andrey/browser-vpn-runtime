"""Browser and VPN runtime readiness boundary."""

import argparse
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from browser_vpn_runtime.config import BrowserRuntimeConfig
from browser_vpn_runtime.openvpn import OpenVpnConfigError, openvpn_config_validate
from browser_vpn_runtime.playwright_profile import PlaywrightProfileState, playwright_profile_materialize


class BrowserRuntimeState(BaseModel):
    """Strict readiness state for browser/VPN runtime prerequisites."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    is_ready: bool
    locale: str
    openvpn_config_name: str
    persistent_profile_path: Path
    problem_list: list[str]
    timezone: str
    viewport_height: int
    viewport_width: int


class PlaywrightRuntimeContext(BaseModel):
    """Runtime context for caller-owned Playwright browser launch."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    locale: str
    materialized_profile_file_path_list: list[Path]
    persistent_profile_path: Path
    timezone: str
    viewport_height: int
    viewport_width: int


class BrowserRuntime:
    """Runtime boundary for browser extraction through Playwright over VPN."""

    def __init__(self, config: BrowserRuntimeConfig) -> None:
        """Initialize runtime boundary.

        Args:
            config: Validated runtime configuration.
        """

        self._config = config

    def playwright_runtime_context_get(self) -> PlaywrightRuntimeContext:
        """Materialize the persistent profile and return Playwright launch settings.

        Returns:
            Runtime context for caller-owned Playwright launch code.
        """

        playwright_profile_state = playwright_profile_materialize(
            data_source_path=self._config.data_source_path,
            target_profile_path=self._config.persistent_profile_path,
        )
        return PlaywrightRuntimeContext(
            locale=self._config.locale,
            materialized_profile_file_path_list=playwright_profile_state.file_path_list,
            persistent_profile_path=playwright_profile_state.profile_path,
            timezone=self._config.timezone,
            viewport_height=self._config.viewport_height,
            viewport_width=self._config.viewport_width,
        )

    def readiness_check(self) -> BrowserRuntimeState:
        """Check OpenVPN, profile, and browser launch prerequisites.

        Returns:
            Strict readiness state.
        """

        problem_list: list[str] = []
        try:
            openvpn_config_state = openvpn_config_validate(self._config.data_source_path)
            openvpn_config_name = openvpn_config_state.openvpn_config_name
        except OpenVpnConfigError as exc:
            openvpn_config_name = self._config.openvpn_config_name
            problem_list.append(f"openvpn_config: {exc}")
        if self._config.require_vpn_route and not self._have_tun_route():
            problem_list.append("vpn_route: tun0 route is not visible in the current network namespace")
        return BrowserRuntimeState(
            is_ready=not problem_list,
            locale=self._config.locale,
            openvpn_config_name=openvpn_config_name,
            persistent_profile_path=self._config.persistent_profile_path,
            problem_list=problem_list,
            timezone=self._config.timezone,
            viewport_height=self._config.viewport_height,
            viewport_width=self._config.viewport_width,
        )

    def _have_tun_route(self) -> bool:
        """Return whether the current network namespace exposes tun0.

        Returns:
            Whether tun0 is visible to the process.
        """

        return Path("/sys/class/net/tun0").exists()


def _args_parse() -> argparse.Namespace:
    """Parse readiness CLI arguments.

    Returns:
        Parsed CLI namespace.
    """

    parser = argparse.ArgumentParser(description="Check browser/VPN runtime readiness.")
    parser.add_argument("--data-source-path", type=Path, required=True)
    parser.add_argument("--openvpn-config-name", required=True)
    parser.add_argument("--persistent-profile-path", type=Path, default=Path("/runtime/playwright_profile"))
    parser.add_argument("--require-vpn-route", action="store_true")
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--viewport-height", type=int, default=720)
    parser.add_argument("--viewport-width", type=int, default=1280)
    return parser.parse_args()


def main() -> int:
    """Run the readiness CLI.

    Returns:
        Process exit code.
    """

    args = _args_parse()
    config = BrowserRuntimeConfig(**vars(args))
    state = BrowserRuntime(config).readiness_check()
    print(state.model_dump_json(indent=2))
    return 0 if state.is_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
