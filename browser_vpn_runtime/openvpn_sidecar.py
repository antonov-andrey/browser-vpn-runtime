"""OpenVPN sidecar startup entrypoint."""

import os
from pathlib import Path

from browser_vpn_runtime.openvpn import openvpn_auth_file_write

DEFAULT_DATA_SOURCE_PATH = Path("/input/.secret")
DEFAULT_RUNTIME_PATH = Path("/runtime")


def openvpn_sidecar_command_argv_get(data_source_path: Path, runtime_path: Path) -> list[str]:
    """Build OpenVPN sidecar argv from strict DataSource validation.

    Args:
        data_source_path: DataSource root path.
        runtime_path: Writable runtime root path.

    Returns:
        OpenVPN command argv.
    """

    openvpn_config_state = openvpn_auth_file_write(data_source_path, runtime_path)
    return [
        "openvpn",
        "--config",
        str(openvpn_config_state.openvpn_config_path),
        "--auth-user-pass",
        str(openvpn_config_state.auth_file_path),
    ]


def main() -> None:
    """Replace the current process with OpenVPN after strict startup validation."""

    command_argv = openvpn_sidecar_command_argv_get(DEFAULT_DATA_SOURCE_PATH, DEFAULT_RUNTIME_PATH)
    os.execvp(command_argv[0], command_argv)


if __name__ == "__main__":
    main()
