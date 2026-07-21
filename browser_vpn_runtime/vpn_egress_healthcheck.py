"""Verify that a VPN gateway can establish a real target connection through SOCKS5."""

import argparse
import socket
import sys

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 1080
DEFAULT_TIMEOUT_SECONDS = 3.0
SOCKS5_VERSION = 5
SOCKS5_AUTH_METHOD_NONE = 0
SOCKS5_ADDRESS_TYPE_IPV4 = 1
SOCKS5_ADDRESS_TYPE_DOMAIN = 3
SOCKS5_ADDRESS_TYPE_IPV6 = 4


def _socket_receive_exact(connection: socket.socket, size: int) -> bytes:
    """Receive an exact number of bytes or fail when the proxy closes early."""

    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise ConnectionError("SOCKS5 proxy closed the connection before completing its response")
        result.extend(chunk)
    return bytes(result)


def vpn_egress_socks5_connect_check(
    *,
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout_seconds: float,
) -> None:
    """Complete one unauthenticated SOCKS5 CONNECT through the VPN gateway.

    A successful local TCP connection to Dante is insufficient because its listening socket
    remains present while OpenVPN reconnects. This check succeeds only after Dante establishes
    a target connection, whose firewall-constrained traffic can leave only through ``tun0``.

    Args:
        proxy_host: SOCKS5 listener host.
        proxy_port: SOCKS5 listener TCP port.
        target_host: Stable external target resolved by the SOCKS5 gateway.
        target_port: External target TCP port.
        timeout_seconds: Timeout applied to connect, send, and receive operations.

    Raises:
        ConnectionError: If the SOCKS5 handshake or target connection fails.
        ValueError: If an argument cannot be represented by this healthcheck contract.
    """

    if not proxy_host:
        raise ValueError("proxy_host must not be empty")
    if not target_host:
        raise ValueError("target_host must not be empty")
    if not 1 <= proxy_port <= 65_535:
        raise ValueError("proxy_port must be between 1 and 65535")
    if not 1 <= target_port <= 65_535:
        raise ValueError("target_port must be between 1 and 65535")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    encoded_target_host = target_host.encode("idna")
    if len(encoded_target_host) > 255:
        raise ValueError("target_host IDNA representation must not exceed 255 bytes")

    with socket.create_connection((proxy_host, proxy_port), timeout=timeout_seconds) as connection:
        connection.settimeout(timeout_seconds)
        connection.sendall(bytes([SOCKS5_VERSION, 1, SOCKS5_AUTH_METHOD_NONE]))
        greeting = _socket_receive_exact(connection, 2)
        if greeting != bytes([SOCKS5_VERSION, SOCKS5_AUTH_METHOD_NONE]):
            raise ConnectionError(f"SOCKS5 proxy rejected unauthenticated negotiation: {greeting.hex()}")

        connection.sendall(
            bytes([SOCKS5_VERSION, 1, 0, SOCKS5_ADDRESS_TYPE_DOMAIN, len(encoded_target_host)])
            + encoded_target_host
            + target_port.to_bytes(2, byteorder="big")
        )
        response_header = _socket_receive_exact(connection, 4)
        if response_header[0] != SOCKS5_VERSION or response_header[2] != 0:
            raise ConnectionError(f"SOCKS5 proxy returned an invalid CONNECT response: {response_header.hex()}")
        if response_header[1] != 0:
            raise ConnectionError(f"SOCKS5 CONNECT failed with reply {response_header[1]}")

        address_type = response_header[3]
        if address_type == SOCKS5_ADDRESS_TYPE_IPV4:
            bound_address_size = 4
        elif address_type == SOCKS5_ADDRESS_TYPE_IPV6:
            bound_address_size = 16
        elif address_type == SOCKS5_ADDRESS_TYPE_DOMAIN:
            bound_address_size = _socket_receive_exact(connection, 1)[0]
        else:
            raise ConnectionError(f"SOCKS5 proxy returned unsupported address type {address_type}")
        _socket_receive_exact(connection, bound_address_size + 2)


def _args_parse() -> argparse.Namespace:
    """Parse the gateway healthcheck command line."""

    parser = argparse.ArgumentParser(description="Verify target connectivity through a SOCKS5 VPN gateway.")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST)
    parser.add_argument("--proxy-port", default=DEFAULT_PROXY_PORT, type=int)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--timeout-seconds", default=DEFAULT_TIMEOUT_SECONDS, type=float)
    return parser.parse_args()


def main() -> None:
    """Exit successfully only after a real target connection through the gateway."""

    args = _args_parse()
    try:
        vpn_egress_socks5_connect_check(
            proxy_host=args.proxy_host,
            proxy_port=args.proxy_port,
            target_host=args.target_host,
            target_port=args.target_port,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as error:
        print(f"VPN egress healthcheck failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
