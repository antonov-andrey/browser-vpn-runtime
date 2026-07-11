"""Strict runtime configuration models."""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_BROWSER_LOCALE = "en-US"
DEFAULT_BROWSER_TIMEZONE = "UTC"


class BrowserLocaleConfig(BaseModel):
    """Validated locale with deterministic browser language representations."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    locale: str = DEFAULT_BROWSER_LOCALE

    @property
    def accept_language(self) -> str:
        """Return the HTTP language preference derived from navigator languages."""

        return ",".join(
            language if index == 0 else f"{language};q={1 - index / 10:.1f}"
            for index, language in enumerate(self.navigator_language_list)
        )

    @property
    def navigator_language_list(self) -> list[str]:
        """Return ordered unique navigator languages for the configured locale."""

        language_list = [self.locale]
        base_language = self.locale.split("-", maxsplit=1)[0]
        for language in [base_language, "en-US", "en"]:
            if language not in language_list:
                language_list.append(language)
        return language_list

    @property
    def profile_language(self) -> str:
        """Return the comma-separated language preference stored by Chromium."""

        return ",".join(self.navigator_language_list)

    @field_validator("locale")
    @classmethod
    def _locale_validate(cls, locale: str) -> str:
        """Validate a browser locale as a BCP 47-style language tag.

        Args:
            locale: Candidate browser locale.

        Returns:
            Validated browser locale.
        """

        if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", locale) is None:
            raise ValueError("locale must be a BCP 47-style language tag")
        return locale


class BrowserRuntimeConfig(BaseModel):
    """Validated browser and VPN runtime configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    data_source_path: Path
    locale_config: BrowserLocaleConfig = Field(default_factory=BrowserLocaleConfig)
    persistent_profile_path: Path = Path("/runtime/playwright_profile")
    timezone: str = DEFAULT_BROWSER_TIMEZONE
    viewport_height: int = Field(default=1080, ge=1)
    viewport_width: int = Field(default=1920, ge=1)
