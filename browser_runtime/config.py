"""Strict browser runtime configuration models."""

import ipaddress
import json
from pathlib import Path
import re
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator
from workflow_container_contract import network_proxy_name_validate

DEFAULT_BROWSER_LOCALE = "en-US"
DEFAULT_BROWSER_TIMEZONE = "UTC"


def network_proxy_url_validate(network_proxy_url: str) -> str:
    """Require one credential-free SOCKS5 endpoint.

    Args:
        network_proxy_url: Candidate run-local network proxy URL.

    Returns:
        Validated unchanged URL.

    Raises:
        ValueError: If the URL is not one safe SOCKS5 endpoint.
    """

    split_network_proxy_url = urlsplit(network_proxy_url)
    try:
        network_proxy_port = split_network_proxy_url.port
    except ValueError as exc:
        raise ValueError("network proxy URL must contain a valid port") from exc
    network_proxy_hostname = split_network_proxy_url.hostname
    if (
        split_network_proxy_url.scheme != "socks5"
        or network_proxy_hostname is None
        or network_proxy_port is None
        or network_proxy_port < 1
        or split_network_proxy_url.username is not None
        or split_network_proxy_url.password is not None
        or split_network_proxy_url.path not in {"", "/"}
        or split_network_proxy_url.query
        or split_network_proxy_url.fragment
    ):
        raise ValueError("network proxy URL must be one credential-free socks5://host:port endpoint")
    try:
        ipaddress.ip_address(network_proxy_hostname)
    except ValueError:
        hostname_label_list = network_proxy_hostname.split(".")
        if len(network_proxy_hostname) > 253 or any(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", hostname_label) is None
            for hostname_label in hostname_label_list
        ):
            raise ValueError("network proxy URL must contain one valid IP address or DNS hostname") from None
    return network_proxy_url


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


class NetworkProxyConfig(BaseModel):
    """Store one immutable exact stable-name to SOCKS5 endpoint map."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    proxy_by_name_map: dict[str, str]

    @classmethod
    def from_path(cls, path: Path) -> Self:
        """Load one immutable platform-provided network proxy map.

        Args:
            path: JSON document containing exactly `proxy_by_name_map`.

        Returns:
            Validated network proxy configuration.

        Raises:
            ValueError: If the document is unreadable or malformed.
        """

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to load network proxy config from {path}: {exc}") from exc
        return cls.model_validate(payload)

    @field_validator("proxy_by_name_map")
    @classmethod
    def proxy_by_name_map_validate(cls, proxy_by_name_map: dict[str, str]) -> dict[str, str]:
        """Validate every exact public name and safe endpoint.

        Args:
            proxy_by_name_map: Candidate exact proxy map.

        Returns:
            Independently copied validated map.
        """

        for network_proxy_name, network_proxy_url in proxy_by_name_map.items():
            network_proxy_name_validate(network_proxy_name)
            network_proxy_url_validate(network_proxy_url)
        return dict(proxy_by_name_map)

    def proxy_url_get(self, network_proxy_name: str | None) -> str | None:
        """Return only the endpoint explicitly named by one caller.

        Args:
            network_proxy_name: Exact stable name, or `None` for direct egress.

        Returns:
            Exact SOCKS5 URL, or `None` for direct egress.

        Raises:
            ValueError: If the supplied name is malformed or unknown.
        """

        if network_proxy_name is None:
            return None
        network_proxy_name_validate(network_proxy_name)
        try:
            return self.proxy_by_name_map[network_proxy_name]
        except KeyError as exc:
            raise ValueError(f"network proxy is unavailable: {network_proxy_name}") from exc
