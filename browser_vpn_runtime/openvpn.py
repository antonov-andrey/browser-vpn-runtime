"""OpenVPN DataSource validation helpers."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from browser_vpn_runtime.config import openvpn_config_name_validate


class OpenVpnConfigError(RuntimeError):
    """Raised when OpenVPN DataSource configuration is invalid."""


class OpenVpnConfigDocument(BaseModel):
    """Strict representation of openvpn/config.json."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    login: str
    openvpn_config_name: str
    password: str

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


class OpenVpnConfigState(BaseModel):
    """Validated OpenVPN config state."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    auth_file_path: Path | None = None
    login: str
    openvpn_config_name: str
    openvpn_config_path: Path
    openvpn_metadata_path: Path
    password: str


def openvpn_config_load(data_source_path: Path) -> OpenVpnConfigDocument:
    """Load and validate openvpn/config.json from a DataSource directory.

    Args:
        data_source_path: DataSource root path.

    Returns:
        Strict OpenVPN config document.

    Raises:
        OpenVpnConfigError: If config metadata is missing or invalid.
    """

    openvpn_metadata_path = data_source_path / "openvpn" / "config.json"
    try:
        payload = json.loads(openvpn_metadata_path.read_text(encoding="utf-8"))
        return OpenVpnConfigDocument(**payload)
    except FileNotFoundError as exc:
        raise OpenVpnConfigError(f"missing OpenVPN metadata file: {openvpn_metadata_path}") from exc
    except json.JSONDecodeError as exc:
        raise OpenVpnConfigError(f"invalid OpenVPN metadata JSON: {openvpn_metadata_path}") from exc
    except ValidationError as exc:
        raise OpenVpnConfigError(f"invalid OpenVPN metadata contract: {openvpn_metadata_path}") from exc


def openvpn_config_validate(data_source_path: Path) -> OpenVpnConfigState:
    """Validate openvpn/config.json and its named .ovpn file.

    Args:
        data_source_path: DataSource root path.

    Returns:
        Validated OpenVPN config state.

    Raises:
        OpenVpnConfigError: If metadata or the named .ovpn file is invalid.
    """

    openvpn_config_document = openvpn_config_load(data_source_path)
    openvpn_metadata_path = data_source_path / "openvpn" / "config.json"
    openvpn_config_path = data_source_path / "openvpn" / openvpn_config_document.openvpn_config_name
    if not openvpn_config_path.is_file():
        raise OpenVpnConfigError(f"missing OpenVPN config file: {openvpn_config_path}")
    return OpenVpnConfigState(
        login=openvpn_config_document.login,
        openvpn_config_name=openvpn_config_document.openvpn_config_name,
        openvpn_config_path=openvpn_config_path,
        openvpn_metadata_path=openvpn_metadata_path,
        password=openvpn_config_document.password,
    )


def openvpn_auth_file_write(data_source_path: Path, runtime_path: Path) -> OpenVpnConfigState:
    """Write OpenVPN auth-user-pass file into pod-local runtime storage.

    Args:
        data_source_path: DataSource root path.
        runtime_path: Writable pod-local runtime directory.

    Returns:
        Validated OpenVPN config state with the generated auth file path.

    Raises:
        OpenVpnConfigError: If metadata or the named .ovpn file is invalid.
    """

    openvpn_config_state = openvpn_config_validate(data_source_path)
    runtime_path.mkdir(parents=True, exist_ok=True)
    auth_file_path = runtime_path / "openvpn-auth.txt"
    auth_file_path.write_text(
        f"{openvpn_config_state.login}\n{openvpn_config_state.password}\n",
        encoding="utf-8",
    )
    auth_file_path.chmod(0o600)
    return OpenVpnConfigState(
        auth_file_path=auth_file_path,
        login=openvpn_config_state.login,
        openvpn_config_name=openvpn_config_state.openvpn_config_name,
        openvpn_config_path=openvpn_config_state.openvpn_config_path,
        openvpn_metadata_path=openvpn_config_state.openvpn_metadata_path,
        password=openvpn_config_state.password,
    )
