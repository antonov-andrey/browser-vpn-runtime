"""Tests for OpenVPN config validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from browser_vpn_runtime.config import BrowserRuntimeConfig
from browser_vpn_runtime.openvpn import OpenVpnConfigError, openvpn_auth_file_write, openvpn_config_validate
from browser_vpn_runtime.openvpn_sidecar import openvpn_sidecar_command_argv_get


def test_runtime_config_rejects_unsafe_openvpn_names(tmp_path: Path) -> None:
    """Reject OpenVPN config names that can escape the openvpn directory."""
    unsafe_name_list = ["../client.ovpn", "/etc/openvpn/client.ovpn", "nested/client.ovpn", "..", "client..ovpn"]

    for unsafe_name in unsafe_name_list:
        with pytest.raises(ValidationError):
            BrowserRuntimeConfig(data_source_path=tmp_path, openvpn_config_name=unsafe_name)


def test_openvpn_config_validate_requires_named_file_under_openvpn(tmp_path: Path) -> None:
    """Validate that config.json names an existing .ovpn file under openvpn."""
    openvpn_path = tmp_path / "openvpn"
    openvpn_path.mkdir()
    (openvpn_path / "config.json").write_text(
        '{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}\n',
        encoding="utf-8",
    )
    (openvpn_path / "client.ovpn").write_text("client\n", encoding="utf-8")

    state = openvpn_config_validate(tmp_path)

    assert state.login == "vpn-user"
    assert state.openvpn_config_name == "client.ovpn"
    assert state.openvpn_config_path == openvpn_path / "client.ovpn"
    assert state.password == "vpn-password"


def test_openvpn_config_validate_fails_when_named_file_is_missing(tmp_path: Path) -> None:
    """Report a missing .ovpn file instead of accepting a dangling config name."""
    openvpn_path = tmp_path / "openvpn"
    openvpn_path.mkdir()
    (openvpn_path / "config.json").write_text(
        '{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}\n',
        encoding="utf-8",
    )

    with pytest.raises(OpenVpnConfigError, match="client.ovpn"):
        openvpn_config_validate(tmp_path)


def test_openvpn_auth_file_write_creates_runtime_auth_file(tmp_path: Path) -> None:
    """Write OpenVPN credentials into writable runtime storage."""
    data_source_path = tmp_path / "data-source"
    openvpn_path = data_source_path / "openvpn"
    openvpn_path.mkdir(parents=True)
    (openvpn_path / "config.json").write_text(
        '{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}\n',
        encoding="utf-8",
    )
    (openvpn_path / "client.ovpn").write_text("client\n", encoding="utf-8")
    runtime_path = tmp_path / "runtime"

    state = openvpn_auth_file_write(data_source_path, runtime_path)

    auth_file_path = runtime_path / "openvpn-auth.txt"
    assert state.auth_file_path == auth_file_path
    assert auth_file_path.read_text(encoding="utf-8") == "vpn-user\nvpn-password\n"
    assert auth_file_path.stat().st_mode & 0o777 == 0o600


def test_openvpn_sidecar_command_uses_validated_runtime_state(tmp_path: Path) -> None:
    """Build sidecar OpenVPN argv from strict config validation and auth-file state."""
    data_source_path = tmp_path / "data-source"
    openvpn_path = data_source_path / "openvpn"
    openvpn_path.mkdir(parents=True)
    (openvpn_path / "config.json").write_text(
        '{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}\n',
        encoding="utf-8",
    )
    (openvpn_path / "client.ovpn").write_text("client\n", encoding="utf-8")
    runtime_path = tmp_path / "runtime"

    command_argv = openvpn_sidecar_command_argv_get(data_source_path, runtime_path)

    assert command_argv == [
        "openvpn",
        "--config",
        str(openvpn_path / "client.ovpn"),
        "--auth-user-pass",
        str(runtime_path / "openvpn-auth.txt"),
    ]


@pytest.mark.parametrize(
    "unsafe_name",
    ["nested/client.ovpn", "../client.ovpn", "/etc/openvpn/client.ovpn"],
)
def test_openvpn_sidecar_command_rejects_unsafe_config_name(tmp_path: Path, unsafe_name: str) -> None:
    """Reject sidecar config names with path separators, traversal, or absolute paths."""
    data_source_path = tmp_path / "data-source"
    openvpn_path = data_source_path / "openvpn"
    openvpn_path.mkdir(parents=True)
    (openvpn_path / "config.json").write_text(
        f'{{"login": "vpn-user", "openvpn_config_name": "{unsafe_name}", "password": "vpn-password"}}\n',
        encoding="utf-8",
    )

    with pytest.raises(OpenVpnConfigError):
        openvpn_sidecar_command_argv_get(data_source_path, tmp_path / "runtime")
