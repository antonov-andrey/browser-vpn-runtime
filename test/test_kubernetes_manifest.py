"""Behavior tests for the reference Kubernetes browser capability."""

from pathlib import Path

import yaml


def _resource_by_kind_and_name_get(resource_list: list[dict[str, object]], kind: str, name: str) -> dict[str, object]:
    """Return one Kubernetes resource by API kind and metadata name.

    Args:
        resource_list: Parsed Kubernetes resource list.
        kind: Required resource kind.
        name: Required metadata name.

    Returns:
        Matching Kubernetes resource.
    """

    return next(
        resource for resource in resource_list if resource["kind"] == kind and resource["metadata"]["name"] == name
    )


def test_kubernetes_manifest_separates_gateway_and_browser_network_ownership() -> None:
    """Run VPN egress and Playwright MCP in distinct pods connected through SOCKS5."""
    resource_list = list(yaml.safe_load_all(Path("deploy/k8s/runtime-capability.yaml").read_text(encoding="utf-8")))
    gateway_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "vpn-egress")
    browser_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "browser-mcp")
    gateway_service = _resource_by_kind_and_name_get(resource_list, "Service", "vpn-egress")
    browser_service = _resource_by_kind_and_name_get(resource_list, "Service", "browser-mcp")

    gateway_pod_spec = gateway_deployment["spec"]["template"]["spec"]
    browser_pod_spec = browser_deployment["spec"]["template"]["spec"]
    gateway_container = gateway_pod_spec["containers"][0]
    browser_container = browser_pod_spec["containers"][0]

    assert [container["name"] for container in gateway_pod_spec["containers"]] == ["vpn-egress"]
    assert gateway_container["command"] == ["browser-vpn-runtime-vpn-egress"]
    assert gateway_container["securityContext"]["capabilities"]["add"] == ["NET_ADMIN"]
    assert gateway_container["securityContext"]["runAsUser"] == 0
    assert gateway_service["spec"]["ports"] == [
        {"name": "socks5", "port": 1080, "protocol": "TCP", "targetPort": "socks5"}
    ]
    assert gateway_service["spec"]["selector"] == gateway_deployment["spec"]["selector"]["matchLabels"]

    assert [container["name"] for container in browser_pod_spec["containers"]] == ["browser-mcp"]
    assert "command" not in browser_container
    assert browser_container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert browser_container["securityContext"]["runAsGroup"] == 1000
    assert browser_container["securityContext"]["runAsUser"] == 1000
    assert browser_pod_spec["securityContext"] == {"fsGroup": 1000}
    assert "NET_ADMIN" not in browser_container["securityContext"].get("capabilities", {}).get("add", [])
    assert all(volume["name"] != "tun-device" for volume in browser_pod_spec["volumes"])
    assert all(mount["mountPath"] != "/dev/net/tun" for mount in browser_container["volumeMounts"])
    assert browser_container["args"] == [
        "browser-vpn-runtime-playwright-mcp-router",
        "--secret-root-path",
        "/input/.secret",
        "--profile-root-path",
        "/runtime/mcp_playwright_profile/profile",
        "--candidate-root-path",
        "/runtime/mcp_playwright_profile/writeback_candidate",
        "--output-root-path",
        "/output/.playwright-mcp",
        "--backend-runtime-root-path",
        "/runtime/playwright_mcp_backend",
        "--host",
        "0.0.0.0",
        "--allowed-hosts",
        "localhost,127.0.0.1,browser-mcp",
        "--port",
        "8931",
        "--vpn-proxy-server",
        "vpn-egress:1080",
    ]
    assert browser_service["spec"]["ports"] == [{"name": "mcp", "port": 8931, "protocol": "TCP", "targetPort": "mcp"}]
    assert browser_service["spec"]["selector"] == browser_deployment["spec"]["selector"]["matchLabels"]


def test_kubernetes_manifest_limits_browser_egress_to_proxy_and_service_dns() -> None:
    """Prevent browser pods from bypassing the SOCKS gateway or cluster DNS."""
    resource_list = list(yaml.safe_load_all(Path("deploy/k8s/runtime-capability.yaml").read_text(encoding="utf-8")))
    network_policy = _resource_by_kind_and_name_get(resource_list, "NetworkPolicy", "browser-mcp-egress-deny")

    assert network_policy["spec"]["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "browser-mcp"}}
    assert network_policy["spec"]["policyTypes"] == ["Egress"]
    assert network_policy["spec"]["egress"] == [
        {
            "ports": [{"port": 1080, "protocol": "TCP"}],
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "vpn-egress"}}}],
        },
        {
            "ports": [{"port": 53, "protocol": "TCP"}, {"port": 53, "protocol": "UDP"}],
            "to": [
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                }
            ],
        },
    ]


def test_kubernetes_manifest_keeps_vpn_secret_and_tun_out_of_browser_pod() -> None:
    """Mount the full secret root and tunnel device only into the VPN gateway."""
    resource_list = list(yaml.safe_load_all(Path("deploy/k8s/runtime-capability.yaml").read_text(encoding="utf-8")))
    gateway_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "vpn-egress")
    browser_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "browser-mcp")
    gateway_container = gateway_deployment["spec"]["template"]["spec"]["containers"][0]
    browser_container = browser_deployment["spec"]["template"]["spec"]["containers"][0]
    gateway_mount_by_path_map = {mount["mountPath"]: mount for mount in gateway_container["volumeMounts"]}
    browser_mount_by_path_map = {mount["mountPath"]: mount for mount in browser_container["volumeMounts"]}

    assert gateway_mount_by_path_map["/input/.secret"]["readOnly"] is True
    assert gateway_mount_by_path_map["/dev/net/tun"]["name"] == "tun-device"
    assert browser_mount_by_path_map["/input/.secret/playwright_profile"] == {
        "mountPath": "/input/.secret/playwright_profile",
        "name": "runtime-secret-root",
        "readOnly": True,
        "subPath": "playwright_profile",
    }
    assert browser_mount_by_path_map["/runtime/mcp_playwright_profile"]["name"] == "profile-writeback"
    assert browser_mount_by_path_map["/runtime/mcp_playwright_profile/profile"]["name"] == "profile-runtime"
    assert "/input/.secret" not in browser_mount_by_path_map


def test_kubernetes_manifest_exposes_only_router_and_has_no_obsolete_writeback_job() -> None:
    """Expose one public router port without recreating the deleted snapshot Job."""

    resource_list = list(yaml.safe_load_all(Path("deploy/k8s/runtime-capability.yaml").read_text(encoding="utf-8")))
    browser_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "browser-mcp")
    browser_container = browser_deployment["spec"]["template"]["spec"]["containers"][0]

    assert browser_container["ports"] == [{"containerPort": 8931, "name": "mcp", "protocol": "TCP"}]
    assert all(resource["kind"] != "Job" for resource in resource_list)
