"""Tests for the VPN egress gateway runtime boundary."""

import socket
from pathlib import Path

import pytest

import browser_vpn_runtime.openvpn as openvpn
from browser_vpn_runtime.openvpn import OpenVpnConfigError, openvpn_auth_file_write
from browser_vpn_runtime.vpn_gateway import VpnEgressGateway


def _vpn_data_source_create(tmp_path: Path) -> Path:
    """Create one valid DataSource with OpenVPN credentials.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        DataSource path.
    """

    data_source_path = tmp_path / "data-source"
    openvpn_path = data_source_path / "openvpn"
    openvpn_path.mkdir(parents=True)
    (openvpn_path / "config.json").write_text(
        '{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}\n',
        encoding="utf-8",
    )
    (openvpn_path / "client.ovpn").write_text("client\n", encoding="utf-8")
    return data_source_path


def test_openvpn_auth_file_write_returns_minimal_launch_config(tmp_path: Path) -> None:
    """Expose only the two paths required to launch OpenVPN."""
    data_source_path = _vpn_data_source_create(tmp_path)

    launch_config = openvpn_auth_file_write(data_source_path, tmp_path / "runtime")

    assert set(type(launch_config).model_fields) == {"auth_file_path", "openvpn_config_path"}
    assert launch_config.auth_file_path.read_text(encoding="utf-8") == "vpn-user\nvpn-password\n"
    assert launch_config.openvpn_config_path == data_source_path / "openvpn" / "client.ovpn"
    assert not hasattr(openvpn, "openvpn_config_validate")


def test_openvpn_auth_file_write_fails_when_named_file_is_missing(tmp_path: Path) -> None:
    """Report a missing .ovpn file instead of accepting a dangling config name."""
    data_source_path = _vpn_data_source_create(tmp_path)
    (data_source_path / "openvpn" / "client.ovpn").unlink()

    with pytest.raises(OpenVpnConfigError, match="client.ovpn"):
        openvpn_auth_file_write(data_source_path, tmp_path / "runtime")


def test_vpn_egress_gateway_writes_authenticated_fail_closed_supervised_runtime(tmp_path: Path) -> None:
    """Generate the Dante, firewall, hook, and supervisor runtime owned by the gateway."""
    data_source_path = _vpn_data_source_create(tmp_path)
    resolv_config_path = tmp_path / "resolv.conf"
    gateway = VpnEgressGateway(
        data_source_path=data_source_path,
        resolv_config_path=resolv_config_path,
        runtime_path=tmp_path / "runtime",
    )

    state = gateway.runtime_prepare()

    assert resolv_config_path.read_text(encoding="utf-8") == "nameserver 1.1.1.1\nnameserver 8.8.8.8\n"
    assert state.auth_file_path.read_text(encoding="utf-8") == "vpn-user\nvpn-password\n"
    assert state.auth_file_path.stat().st_mode & 0o777 == 0o600
    assert state.dante_config_path.read_text(encoding="utf-8") == "\n".join(
        [
            "logoutput: stderr",
            "internal: 0.0.0.0 port = 1080",
            "external: tun0",
            "clientmethod: none",
            "socksmethod: none",
            "user.privileged: root",
            "user.unprivileged: vpnproxy",
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
    assert state.firewall_path.read_text(encoding="utf-8") == "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            "iptables -N VPNPROXY_EGRESS 2>/dev/null || true",
            "iptables -F VPNPROXY_EGRESS",
            "iptables -C OUTPUT -m owner --uid-owner vpnproxy -j VPNPROXY_EGRESS 2>/dev/null || iptables -A OUTPUT -m owner --uid-owner vpnproxy -j VPNPROXY_EGRESS",
            "iptables -A VPNPROXY_EGRESS -m conntrack --ctdir REPLY --ctstate ESTABLISHED,RELATED -j ACCEPT",
            "iptables -A VPNPROXY_EGRESS -o tun0 -m conntrack --ctdir ORIGINAL --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT",
            "iptables -A VPNPROXY_EGRESS -j DROP",
            "ip6tables -N VPNPROXY_EGRESS 2>/dev/null || true",
            "ip6tables -F VPNPROXY_EGRESS",
            "ip6tables -C OUTPUT -m owner --uid-owner vpnproxy -j VPNPROXY_EGRESS 2>/dev/null || ip6tables -A OUTPUT -m owner --uid-owner vpnproxy -j VPNPROXY_EGRESS",
            "ip6tables -A VPNPROXY_EGRESS -m conntrack --ctdir REPLY --ctstate ESTABLISHED,RELATED -j ACCEPT",
            "ip6tables -A VPNPROXY_EGRESS -o tun0 -m conntrack --ctdir ORIGINAL --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT",
            "ip6tables -A VPNPROXY_EGRESS -j DROP",
            "",
        ]
    )
    assert state.openvpn_up_hook_path.read_text(encoding="utf-8") == "\n".join(
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
    assert state.openvpn_down_hook_path.read_text(encoding="utf-8") == "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            f"supervisorctl -c {state.supervisor_config_path} status sockd | grep -q RUNNING && supervisorctl -c {state.supervisor_config_path} signal STOP sockd || true",
            "",
        ]
    )
    supervisor_config = state.supervisor_config_path.read_text(encoding="utf-8")
    assert (
        f"command=/usr/sbin/openvpn --config {data_source_path / 'openvpn' / 'client.ovpn'} --auth-user-pass {state.auth_file_path} --persist-tun --script-security 2 --up {state.openvpn_up_hook_path} --down {state.openvpn_down_hook_path} --down-pre --up-restart"
        in supervisor_config
    )
    assert f"command=/usr/sbin/sockd -f {state.dante_config_path}" in supervisor_config
    assert (
        "[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface"
        in supervisor_config
    )
    assert "[program:sockd]\nautostart=false\nautorestart=true" in supervisor_config


def test_vpn_egress_gateway_resolves_remote_before_installing_tunnel_dns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin VPN endpoint addresses before target DNS becomes tunnel-only.

    Args:
        monkeypatch: Pytest patch fixture.
        tmp_path: Pytest temporary directory.
    """

    data_source_path = _vpn_data_source_create(tmp_path)
    (data_source_path / "openvpn" / "client.ovpn").write_text(
        "client\nremote vpn.example 8000 tcp\n",
        encoding="utf-8",
    )
    hosts_config_path = tmp_path / "hosts"
    hosts_config_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    resolv_config_path = tmp_path / "resolv.conf"
    resolv_config_path.write_text("nameserver 127.0.0.11\n", encoding="utf-8")
    resolver_config_text_list: list[str] = []

    def getaddrinfo(host: str, port: None, *, type: socket.SocketKind) -> list[tuple[object, ...]]:
        """Return a deterministic VPN endpoint while recording resolver ordering.

        Args:
            host: VPN endpoint host name.
            port: Unused service port.
            type: Requested socket type.

        Returns:
            Synthetic getaddrinfo response.
        """

        assert host == "vpn.example"
        assert port is None
        assert type is socket.SOCK_STREAM
        resolver_config_text_list.append(resolv_config_path.read_text(encoding="utf-8"))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.7", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.7", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    gateway = VpnEgressGateway(
        data_source_path=data_source_path,
        hosts_config_path=hosts_config_path,
        resolv_config_path=resolv_config_path,
        runtime_path=tmp_path / "runtime",
    )

    gateway.runtime_prepare()

    assert resolver_config_text_list == ["nameserver 127.0.0.11\n"]
    assert hosts_config_path.read_text(encoding="utf-8") == ("127.0.0.1 localhost\n203.0.113.7 vpn.example\n")
    assert resolv_config_path.read_text(encoding="utf-8") == "nameserver 1.1.1.1\nnameserver 8.8.8.8\n"
