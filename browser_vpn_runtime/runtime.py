"""Browser launch-context boundary."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from browser_vpn_runtime.config import BrowserLocaleConfig, BrowserRuntimeConfig
from browser_vpn_runtime.playwright_profile import playwright_profile_materialize


class PlaywrightRuntimeContext(BaseModel):
    """Runtime context for caller-owned Playwright browser launch."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    locale_config: BrowserLocaleConfig
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
            locale_config=self._config.locale_config,
            materialized_profile_file_path_list=playwright_profile_state.file_path_list,
            persistent_profile_path=playwright_profile_state.profile_path,
            timezone=self._config.timezone,
            viewport_height=self._config.viewport_height,
            viewport_width=self._config.viewport_width,
        )
