"""OpenVPN secret root validation helpers."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class OpenVpnConfigError(RuntimeError):
    """Raised when OpenVPN secret root configuration is invalid."""


class _OpenVpnConfigDocument(BaseModel):
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


class OpenVpnLaunchConfig(BaseModel):
    """Paths required to launch OpenVPN with generated authentication."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    auth_file_path: Path
    openvpn_config_path: Path


def _openvpn_config_load(secret_root_path: Path) -> _OpenVpnConfigDocument:
    """Load and validate openvpn/config.json from a secret root directory.

    Args:
        secret_root_path: Read-only secret root path.

    Returns:
        Strict OpenVPN config document.

    Raises:
        OpenVpnConfigError: If config metadata is missing or invalid.
    """

    openvpn_metadata_path = secret_root_path / "openvpn" / "config.json"
    try:
        payload = json.loads(openvpn_metadata_path.read_text(encoding="utf-8"))
        return _OpenVpnConfigDocument(**payload)
    except FileNotFoundError as exc:
        raise OpenVpnConfigError(f"missing OpenVPN metadata file: {openvpn_metadata_path}") from exc
    except json.JSONDecodeError as exc:
        raise OpenVpnConfigError(f"invalid OpenVPN metadata JSON: {openvpn_metadata_path}") from exc
    except ValidationError as exc:
        raise OpenVpnConfigError(f"invalid OpenVPN metadata contract: {openvpn_metadata_path}") from exc


def openvpn_auth_file_write(secret_root_path: Path, runtime_path: Path) -> OpenVpnLaunchConfig:
    """Write OpenVPN auth-user-pass file into pod-local runtime storage.

    Args:
        secret_root_path: Read-only secret root path.
        runtime_path: Writable pod-local runtime directory.

    Returns:
        Minimal OpenVPN launch configuration.

    Raises:
        OpenVpnConfigError: If metadata or the named .ovpn file is invalid.
    """

    openvpn_config_document = _openvpn_config_load(secret_root_path)
    openvpn_config_path = secret_root_path / "openvpn" / openvpn_config_document.openvpn_config_name
    if not openvpn_config_path.is_file():
        raise OpenVpnConfigError(f"missing OpenVPN config file: {openvpn_config_path}")
    runtime_path.mkdir(parents=True, exist_ok=True)
    auth_file_path = runtime_path / "openvpn-auth.txt"
    auth_file_path.write_text(
        f"{openvpn_config_document.login}\n{openvpn_config_document.password}\n",
        encoding="utf-8",
    )
    auth_file_path.chmod(0o600)
    return OpenVpnLaunchConfig(
        auth_file_path=auth_file_path,
        openvpn_config_path=openvpn_config_path,
    )
