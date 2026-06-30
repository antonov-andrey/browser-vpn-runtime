"""Strict runtime configuration models."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


def openvpn_config_name_validate(openvpn_config_name: str) -> str:
    """Validate one OpenVPN config file name.

    Args:
        openvpn_config_name: Config file name from runtime config or openvpn/config.json.

    Returns:
        Validated config file name.
    """

    if not openvpn_config_name:
        raise ValueError("openvpn_config_name must not be empty")
    if "/" in openvpn_config_name:
        raise ValueError("openvpn_config_name must be one file name without '/'")
    if ".." in openvpn_config_name:
        raise ValueError("openvpn_config_name must not contain '..'")
    if Path(openvpn_config_name).is_absolute():
        raise ValueError("openvpn_config_name must not be an absolute path")
    if not openvpn_config_name.endswith(".ovpn"):
        raise ValueError("openvpn_config_name must name a .ovpn file")
    return openvpn_config_name


class BrowserRuntimeConfig(BaseModel):
    """Validated browser and VPN runtime configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    data_source_path: Path
    locale: str = "en-US"
    openvpn_config_name: str
    persistent_profile_path: Path = Path("/runtime/playwright_profile")
    require_vpn_route: bool = False
    timezone: str = "UTC"
    viewport_height: int = Field(default=720, ge=1)
    viewport_width: int = Field(default=1280, ge=1)

    @field_validator("openvpn_config_name")
    @classmethod
    def _openvpn_config_name_validate(cls, openvpn_config_name: str) -> str:
        """Validate OpenVPN config file name syntax.

        Args:
            openvpn_config_name: Candidate OpenVPN config file name.

        Returns:
            Validated OpenVPN config file name.
        """

        return openvpn_config_name_validate(openvpn_config_name)

    @property
    def codex_profile_path(self) -> Path:
        """Return the conventional Codex profile path inside the DataSource."""

        return self.data_source_path / "codex_profile"

    @property
    def openvpn_config_path(self) -> Path:
        """Return the expected OpenVPN config path inside the DataSource."""

        return self.openvpn_path / self.openvpn_config_name

    @property
    def openvpn_metadata_path(self) -> Path:
        """Return the OpenVPN metadata config path inside the DataSource."""

        return self.openvpn_path / "config.json"

    @property
    def openvpn_path(self) -> Path:
        """Return the conventional OpenVPN DataSource directory."""

        return self.data_source_path / "openvpn"

    @property
    def playwright_profile_path(self) -> Path:
        """Return the conventional Playwright profile path inside the DataSource."""

        return self.data_source_path / "playwright_profile"
