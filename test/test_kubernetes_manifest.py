"""Behavior tests for the reference Kubernetes browser capability."""

import json
from pathlib import Path

import yaml


def _resource_by_kind_and_name_get(resource_list: list[dict[str, object]], kind: str, name: str) -> dict[str, object]:
    """Return one Kubernetes resource by API kind and metadata name.

    Args:
        resource_list: Parsed Kubernetes resource list.
        kind: Required resource kind.
        name: Required resource name.

    Returns:
        Matching Kubernetes resource.
    """

    return next(
        resource for resource in resource_list if resource["kind"] == kind and resource["metadata"]["name"] == name
    )


def _resource_list_get() -> list[dict[str, object]]:
    """Parse the complete reference manifest.

    Returns:
        Kubernetes resource documents.
    """

    return list(yaml.safe_load_all(Path("deploy/k8s/runtime-capability.yaml").read_text(encoding="utf-8")))


def test_kubernetes_manifest_mounts_platform_proxy_map_and_browser_profile_only() -> None:
    """Pass safe proxy endpoints and browser profile state without tunnel credentials or devices."""

    resource_list = _resource_list_get()
    browser_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "browser-mcp")
    network_proxy_config = _resource_by_kind_and_name_get(
        resource_list,
        "ConfigMap",
        "browser-runtime-network-proxy",
    )
    browser_pod_spec = browser_deployment["spec"]["template"]["spec"]
    browser_container = browser_pod_spec["containers"][0]
    mount_by_path_map = {mount["mountPath"]: mount for mount in browser_container["volumeMounts"]}

    assert json.loads(network_proxy_config["data"]["network-proxy.json"]) == {"proxy_by_name_map": {}}
    assert mount_by_path_map["/runtime-config"] == {
        "mountPath": "/runtime-config",
        "name": "network-proxy-config",
        "readOnly": True,
    }
    assert mount_by_path_map["/input/.secret/playwright_profile"] == {
        "mountPath": "/input/.secret/playwright_profile",
        "name": "profile-source",
        "readOnly": True,
    }
    assert "/input/.secret" not in mount_by_path_map
    assert "/dev/net/tun" not in mount_by_path_map
    assert all(volume["name"] != "tun-device" for volume in browser_pod_spec["volumes"])


def test_kubernetes_manifest_runs_only_the_non_root_browser_router() -> None:
    """Launch one unprivileged browser process with the immutable proxy-map path."""

    resource_list = _resource_list_get()
    browser_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "browser-mcp")
    browser_pod_spec = browser_deployment["spec"]["template"]["spec"]
    browser_container = browser_pod_spec["containers"][0]

    assert [resource["metadata"]["name"] for resource in resource_list if resource["kind"] == "Deployment"] == [
        "browser-mcp"
    ]
    assert [container["name"] for container in browser_pod_spec["containers"]] == ["browser-mcp"]
    assert browser_pod_spec["automountServiceAccountToken"] is False
    assert browser_pod_spec["securityContext"] == {"fsGroup": 1000}
    assert browser_container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsGroup": 1000,
        "runAsNonRoot": True,
        "runAsUser": 1000,
    }
    assert browser_container["args"] == [
        "browser-runtime-playwright-mcp-router",
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
        "--network-proxy-config-path",
        "/runtime-config/network-proxy.json",
        "--host",
        "0.0.0.0",
        "--allowed-hosts",
        "localhost,127.0.0.1,browser-mcp",
        "--port",
        "8931",
    ]


def test_kubernetes_manifest_exposes_only_the_public_browser_router() -> None:
    """Expose the MCP router without creating a gateway or snapshot Job."""

    resource_list = _resource_list_get()
    browser_deployment = _resource_by_kind_and_name_get(resource_list, "Deployment", "browser-mcp")
    browser_service = _resource_by_kind_and_name_get(resource_list, "Service", "browser-mcp")
    browser_container = browser_deployment["spec"]["template"]["spec"]["containers"][0]

    assert [resource["metadata"]["name"] for resource in resource_list if resource["kind"] == "Service"] == [
        "browser-mcp"
    ]
    assert browser_container["ports"] == [{"containerPort": 8931, "name": "mcp", "protocol": "TCP"}]
    assert browser_service["spec"]["ports"] == [{"name": "mcp", "port": 8931, "protocol": "TCP", "targetPort": "mcp"}]
    assert browser_service["spec"]["selector"] == browser_deployment["spec"]["selector"]["matchLabels"]
    assert all(resource["kind"] not in {"Job", "NetworkPolicy"} for resource in resource_list)
