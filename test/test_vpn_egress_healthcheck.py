"""Tests for the VPN egress SOCKS5 healthcheck."""

import socket

import pytest

from browser_vpn_runtime.vpn_egress_healthcheck import vpn_egress_socks5_connect_check


class FakeSocket:
    """Minimal socket that returns a prepared SOCKS5 server response."""

    def __init__(self, response: bytes):
        self._response = bytearray(response)
        self.sent_data_list: list[bytes] = []
        self.timeout: float | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent_data_list.append(data)

    def recv(self, size: int) -> bytes:
        if not self._response:
            return b""
        chunk_size = min(size, 2, len(self._response))
        result = bytes(self._response[:chunk_size])
        del self._response[:chunk_size]
        return result


def test_vpn_egress_healthcheck_completes_socks5_target_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require a successful target CONNECT instead of accepting only the proxy listener."""

    fake_socket = FakeSocket(b"\x05\x00" b"\x05\x00\x00\x01" b"\x7f\x00\x00\x01" b"\x01\xbb")

    def create_connection(address: tuple[str, int], timeout: float):
        assert address == ("127.0.0.1", 1080)
        assert timeout == 3.0
        return fake_socket

    monkeypatch.setattr(socket, "create_connection", create_connection)

    vpn_egress_socks5_connect_check(
        proxy_host="127.0.0.1",
        proxy_port=1080,
        target_host="one.one.one.one",
        target_port=443,
        timeout_seconds=3.0,
    )

    assert fake_socket.timeout == 3.0
    assert fake_socket.sent_data_list == [
        b"\x05\x01\x00",
        b"\x05\x01\x00\x03\x0fone.one.one.one\x01\xbb",
    ]


def test_vpn_egress_healthcheck_rejects_failed_target_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a SOCKS target failure even when the local listener accepted the connection."""

    fake_socket = FakeSocket(b"\x05\x00" b"\x05\x05\x00\x01" b"\x00\x00\x00\x00" b"\x00\x00")
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: fake_socket)

    with pytest.raises(ConnectionError, match="SOCKS5 CONNECT failed with reply 5"):
        vpn_egress_socks5_connect_check(
            proxy_host="127.0.0.1",
            proxy_port=1080,
            target_host="one.one.one.one",
            target_port=443,
            timeout_seconds=3.0,
        )


def test_vpn_egress_healthcheck_rejects_incomplete_proxy_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the proxy stops responding during the SOCKS5 handshake."""

    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: FakeSocket(b"\x05"))

    with pytest.raises(ConnectionError, match="closed the connection"):
        vpn_egress_socks5_connect_check(
            proxy_host="127.0.0.1",
            proxy_port=1080,
            target_host="one.one.one.one",
            target_port=443,
            timeout_seconds=3.0,
        )
