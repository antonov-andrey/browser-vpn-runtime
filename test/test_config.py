"""Behavior tests for strict browser runtime configuration."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from browser_runtime.config import NetworkProxyConfig


def test_network_proxy_config_loads_exact_map_and_supports_direct_mode(tmp_path: Path) -> None:
    """Load the immutable map while preserving exact names and endpoint values."""

    config_path = tmp_path / "network-proxy.json"
    config_path.write_text(
        json.dumps(
            {
                "proxy_by_name_map": {
                    "user-a/proxy_a": "socks5://proxy-a:1080",
                    "user-b/proxy_b": "socks5://proxy-b:1080",
                }
            }
        ),
        encoding="utf-8",
    )

    config = NetworkProxyConfig.from_path(config_path)

    assert config.proxy_url_get(None) is None
    assert config.proxy_url_get("user-a/proxy_a") == "socks5://proxy-a:1080"
    assert config.proxy_url_get("user-b/proxy_b") == "socks5://proxy-b:1080"


@pytest.mark.parametrize(
    "proxy_by_name_map",
    [
        {"unknown": "socks5://proxy-a:1080"},
        {"user-a/proxy_a": "http://proxy-a:1080"},
        {"user-a/proxy_a": "socks5://login:password@proxy-a:1080"},
    ],
)
def test_network_proxy_config_rejects_malformed_names_and_unsafe_urls(
    proxy_by_name_map: dict[str, str],
) -> None:
    """Reject configuration that is not an exact public-name to safe-SOCKS map."""

    with pytest.raises((ValidationError, ValueError)):
        NetworkProxyConfig(proxy_by_name_map=proxy_by_name_map)


def test_network_proxy_config_rejects_unknown_exact_name() -> None:
    """Fail exact lookup instead of selecting a replacement proxy."""

    config = NetworkProxyConfig(proxy_by_name_map={"user-a/proxy_a": "socks5://proxy-a:1080"})

    with pytest.raises(ValueError, match="network proxy is unavailable: user-b/proxy_b"):
        config.proxy_url_get("user-b/proxy_b")
