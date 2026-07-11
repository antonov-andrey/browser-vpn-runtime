"""Runtime-owned OpenVPN and Dante SOCKS5 egress gateway."""

import argparse
import ipaddress
import os
import shlex
import socket
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from browser_vpn_runtime.openvpn import OpenVpnLaunchConfig, openvpn_auth_file_write

DEFAULT_DATA_SOURCE_PATH = Path("/input/.secret")
DEFAULT_HOSTS_CONFIG_PATH = Path("/etc/hosts")
DEFAULT_RESOLV_CONFIG_PATH = Path("/etc/resolv.conf")
DEFAULT_RUNTIME_PATH = Path("/runtime")
DANTE_PORT = 1080
DANTE_USER = "vpnproxy"
VPN_RESOLV_CONFIG_TEXT = "nameserver 1.1.1.1\nnameserver 8.8.8.8\n"


class VpnEgressGatewayState(BaseModel):
    """Paths of gateway artifacts prepared in writable runtime storage."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    auth_file_path: Path
    dante_config_path: Path
    firewall_path: Path
    openvpn_down_hook_path: Path
    openvpn_up_hook_path: Path
    supervisor_config_path: Path


class VpnEgressGateway:
    """Prepare and launch a fail-closed SOCKS5 gateway over OpenVPN."""

    def __init__(
        self,
        *,
        data_source_path: Path,
        hosts_config_path: Path = DEFAULT_HOSTS_CONFIG_PATH,
        resolv_config_path: Path = DEFAULT_RESOLV_CONFIG_PATH,
        runtime_path: Path,
    ) -> None:
        """Initialize the gateway artifact owner.

        Args:
            data_source_path: Read-only DataSource containing OpenVPN configuration.
            hosts_config_path: Hosts file used to pin VPN endpoints across reconnects.
            resolv_config_path: Resolver file used by OpenVPN and Dante.
            runtime_path: Writable directory for generated runtime artifacts.
        """

        self._data_source_path = data_source_path
        self._hosts_config_path = hosts_config_path
        self._resolv_config_path = resolv_config_path
        self._runtime_path = runtime_path / "vpn-egress"

    def firewall_install(self, state: VpnEgressGatewayState) -> None:
        """Install IPv4 and IPv6 output rules before Dante can proxy traffic.

        Args:
            state: Generated gateway runtime paths.
        """

        subprocess.run([str(state.firewall_path)], check=True)

    def runtime_prepare(self) -> VpnEgressGatewayState:
        """Validate VPN input and write all gateway-owned runtime artifacts.

        Returns:
            Prepared gateway artifact paths.
        """

        self._runtime_path.mkdir(parents=True, exist_ok=True)
        openvpn_launch_config = openvpn_auth_file_write(self._data_source_path, self._runtime_path)
        self._openvpn_remote_host_pin(openvpn_launch_config.openvpn_config_path)
        self._resolv_config_path.write_text(VPN_RESOLV_CONFIG_TEXT, encoding="utf-8")
        dante_config_path = self._runtime_path / "sockd.conf"
        firewall_path = self._runtime_path / "firewall.sh"
        openvpn_down_hook_path = self._runtime_path / "openvpn-down.sh"
        openvpn_up_hook_path = self._runtime_path / "openvpn-up.sh"
        supervisor_config_path = self._runtime_path / "supervisord.conf"
        state = VpnEgressGatewayState(
            auth_file_path=openvpn_launch_config.auth_file_path,
            dante_config_path=dante_config_path,
            firewall_path=firewall_path,
            openvpn_down_hook_path=openvpn_down_hook_path,
            openvpn_up_hook_path=openvpn_up_hook_path,
            supervisor_config_path=supervisor_config_path,
        )
        dante_config_path.write_text(self._dante_config_get(), encoding="utf-8")
        firewall_path.write_text(self._firewall_script_get(), encoding="utf-8")
        openvpn_down_hook_path.write_text(self._openvpn_down_hook_get(state), encoding="utf-8")
        openvpn_up_hook_path.write_text(self._openvpn_up_hook_get(state), encoding="utf-8")
        supervisor_config_path.write_text(self._supervisor_config_get(openvpn_launch_config, state), encoding="utf-8")
        for executable_path in [firewall_path, openvpn_down_hook_path, openvpn_up_hook_path]:
            executable_path.chmod(0o700)
        return state

    def supervisor_command_argv_get(self, state: VpnEgressGatewayState) -> list[str]:
        """Return the foreground supervisor command for prepared gateway state.

        Args:
            state: Generated gateway runtime paths.

        Returns:
            Supervisor command argv.
        """

        return ["supervisord", "-n", "-c", str(state.supervisor_config_path)]

    def _openvpn_remote_host_pin(self, openvpn_config_path: Path) -> None:
        """Resolve configured VPN endpoints before installing tunnel-only DNS.

        Args:
            openvpn_config_path: Validated OpenVPN client configuration.
        """

        host_entry_list: list[str] = []
        for config_line in openvpn_config_path.read_text(encoding="utf-8").splitlines():
            stripped_line = config_line.strip()
            if not stripped_line or stripped_line.startswith(("#", ";")):
                continue
            token_list = shlex.split(stripped_line, comments=True)
            if len(token_list) < 2 or token_list[0] != "remote":
                continue
            host = token_list[1]
            try:
                ipaddress.ip_address(host)
            except ValueError:
                address_list = list(
                    dict.fromkeys(
                        address_info[4][0] for address_info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
                    )
                )
                host_entry_list.extend(f"{address} {host}" for address in address_list)
        if not host_entry_list:
            return
        hosts_config_text = self._hosts_config_path.read_text(encoding="utf-8")
        if hosts_config_text and not hosts_config_text.endswith("\n"):
            hosts_config_text += "\n"
        hosts_config_text += "\n".join(host_entry_list) + "\n"
        self._hosts_config_path.write_text(hosts_config_text, encoding="utf-8")

    def _dante_config_get(self) -> str:
        """Return strict Dante configuration for the SOCKS5 listener.

        Returns:
            Dante server configuration text.
        """

        return "\n".join(
            [
                "logoutput: stderr",
                f"internal: 0.0.0.0 port = {DANTE_PORT}",
                "external: tun0",
                "clientmethod: none",
                "socksmethod: none",
                "user.privileged: root",
                f"user.unprivileged: {DANTE_USER}",
                "client pass {",
                "    from: 0.0.0.0/0 to: 0.0.0.0/0",
                "}",
                "socks pass {",
                "    from: 0.0.0.0/0 to: 0.0.0.0/0",
                "    command: connect",
                "}",
                "",
            ]
        )

    def _firewall_script_get(self) -> str:
        """Return firewall rules that deny Dante fallback outside the VPN tunnel.

        Returns:
            POSIX shell firewall script.
        """

        return "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                "iptables -N VPNPROXY_EGRESS 2>/dev/null || true",
                "iptables -F VPNPROXY_EGRESS",
                f"iptables -C OUTPUT -m owner --uid-owner {DANTE_USER} -j VPNPROXY_EGRESS 2>/dev/null || iptables -A OUTPUT -m owner --uid-owner {DANTE_USER} -j VPNPROXY_EGRESS",
                "iptables -A VPNPROXY_EGRESS -m conntrack --ctdir REPLY --ctstate ESTABLISHED,RELATED -j ACCEPT",
                "iptables -A VPNPROXY_EGRESS -o tun0 -m conntrack --ctdir ORIGINAL --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT",
                "iptables -A VPNPROXY_EGRESS -j DROP",
                "ip6tables -N VPNPROXY_EGRESS 2>/dev/null || true",
                "ip6tables -F VPNPROXY_EGRESS",
                f"ip6tables -C OUTPUT -m owner --uid-owner {DANTE_USER} -j VPNPROXY_EGRESS 2>/dev/null || ip6tables -A OUTPUT -m owner --uid-owner {DANTE_USER} -j VPNPROXY_EGRESS",
                "ip6tables -A VPNPROXY_EGRESS -m conntrack --ctdir REPLY --ctstate ESTABLISHED,RELATED -j ACCEPT",
                "ip6tables -A VPNPROXY_EGRESS -o tun0 -m conntrack --ctdir ORIGINAL --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT",
                "ip6tables -A VPNPROXY_EGRESS -j DROP",
                "",
            ]
        )

    def _openvpn_down_hook_get(self, state: VpnEgressGatewayState) -> str:
        """Return the hook that pauses Dante while tunnel traffic is unavailable.

        Args:
            state: Generated gateway runtime paths.

        Returns:
            POSIX shell hook text.
        """

        return "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                f"supervisorctl -c {state.supervisor_config_path} status sockd | grep -q RUNNING && supervisorctl -c {state.supervisor_config_path} signal STOP sockd || true",
                "",
            ]
        )

    def _openvpn_up_hook_get(self, state: VpnEgressGatewayState) -> str:
        """Return the hook that starts or reloads Dante after OpenVPN is up.

        Args:
            state: Generated gateway runtime paths.

        Returns:
            POSIX shell hook text.
        """

        return "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                f"if supervisorctl -c {state.supervisor_config_path} status sockd | grep -q RUNNING; then",
                f"    supervisorctl -c {state.supervisor_config_path} signal CONT sockd",
                f"    supervisorctl -c {state.supervisor_config_path} signal HUP sockd",
                "else",
                f"    supervisorctl -c {state.supervisor_config_path} start sockd",
                "fi",
                "",
            ]
        )

    def _supervisor_config_get(
        self,
        openvpn_launch_config: OpenVpnLaunchConfig,
        state: VpnEgressGatewayState,
    ) -> str:
        """Return foreground supervisor configuration for OpenVPN and Dante.

        Args:
            openvpn_launch_config: Validated OpenVPN config and generated auth file.
            state: Generated gateway runtime paths.

        Returns:
            Supervisor configuration text.
        """

        return "\n".join(
            [
                "[supervisord]",
                "logfile=/dev/null",
                "logfile_maxbytes=0",
                "nodaemon=true",
                f"pidfile={self._runtime_path / 'supervisord.pid'}",
                "",
                "[unix_http_server]",
                f"file={self._runtime_path / 'supervisor.sock'}",
                "chmod=0700",
                "",
                "[rpcinterface:supervisor]",
                "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface",
                "",
                "[supervisorctl]",
                f"serverurl=unix://{self._runtime_path / 'supervisor.sock'}",
                "",
                "[program:openvpn]",
                f"command=/usr/sbin/openvpn --config {openvpn_launch_config.openvpn_config_path} --auth-user-pass {state.auth_file_path} --persist-tun --script-security 2 --up {state.openvpn_up_hook_path} --down {state.openvpn_down_hook_path} --down-pre --up-restart",
                "autorestart=true",
                "priority=10",
                "startsecs=0",
                "stopasgroup=true",
                "killasgroup=true",
                "",
                "[program:sockd]",
                "autostart=false",
                "autorestart=true",
                f"command=/usr/sbin/sockd -f {state.dante_config_path}",
                "priority=20",
                "startsecs=0",
                "stopasgroup=true",
                "killasgroup=true",
                "",
            ]
        )


def _args_parse() -> argparse.Namespace:
    """Parse gateway entrypoint configuration.

    Returns:
        Parsed gateway CLI arguments.
    """

    parser = argparse.ArgumentParser(description="Run the browser VPN egress gateway.")
    parser.add_argument("--data-source-path", default=DEFAULT_DATA_SOURCE_PATH, type=Path)
    parser.add_argument("--runtime-path", default=DEFAULT_RUNTIME_PATH, type=Path)
    return parser.parse_args()


def main() -> None:
    """Prepare gateway state, install firewall policy, and start supervisor."""

    args = _args_parse()
    gateway = VpnEgressGateway(data_source_path=args.data_source_path, runtime_path=args.runtime_path)
    state = gateway.runtime_prepare()
    gateway.firewall_install(state)
    command_argv = gateway.supervisor_command_argv_get(state)
    os.execvp(command_argv[0], command_argv)


if __name__ == "__main__":
    main()
