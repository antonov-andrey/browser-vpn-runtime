# Browser VPN Runtime Design

## Purpose

`browser-vpn-runtime` is a reusable runtime capability for workflows that need browser automation over an OpenVPN connection. It owns runtime contracts, secret layout validation, and Playwright profile directory movement. It does not own domain extraction, marketplace schemas, brand logic, or workflow orchestration policy.

## Secret Layout

The private DataSource mount has four conventional responsibilities:

```text
<data-source>/
  openvpn/
    config.json
    <name>.ovpn
  playwright_profile/
    ...
  codex_profile/
    ...
```

`openvpn/config.json` is optional. When it exists, OpenVPN is enabled, the file selects the OpenVPN file through `openvpn_config_name`, and it stores `login` plus `password` for `--auth-user-pass`. The config name is one local `.ovpn` file name. It must not contain `/`, must not contain `..`, must not be an absolute path, and must resolve to an existing file inside `openvpn/`. When `openvpn/config.json` does not exist, browser runtime runs without VPN.

`playwright_profile/**` is an optional directory tree copied into the pod-local runtime directory. When it does not exist, browser runtime starts with an empty profile. Zip-based profile import/export is intentionally outside this contract because browser profile state is already a filesystem tree and Kubernetes volumes can mount it directly.

`codex_profile/**` is an optional reserved sibling tree for agent or Codex runtime state. This package does not inspect it, so callers can evolve that profile independently.

## Kubernetes Boundary

The reference Kubernetes shape is one pod with two cooperating containers:

- `openvpn`: sidecar container that owns VPN process startup and tunnel lifecycle.
- `browser-runtime`: application or workflow container that owns Playwright launch and extraction behavior.

Both containers share the pod network namespace. The browser container therefore uses the sidecar network path without implementing VPN mechanics itself. The DataSource secret volume is mounted read-only, and `/runtime` is writable pod-local state. The `openvpn` sidecar creates `/runtime/openvpn-auth.txt` from `openvpn/config.json`, sets mode `0600`, and passes it to OpenVPN with `--auth-user-pass`.

This repository provides only a reference manifest. Production consumers should set their own image names, resource limits, probes, and secret names.

## OpenVPN Boundary

OpenVPN config validation has two layers:

- `BrowserRuntimeConfig` validates the user-facing `openvpn_config_name` syntax when it is not empty.
- `openvpn_config_validate(...)` reads `openvpn/config.json`, validates the same syntax, and checks that the named file exists under `openvpn/`.
- `openvpn_auth_file_write(...)` writes the runtime `--auth-user-pass` file into pod-local writable storage without mutating the private DataSource mount.

The runtime never accepts fallback names, alternate paths, absolute paths, or path traversal. Absent `openvpn/config.json` means no-VPN runtime. Invalid present config returns a not-ready runtime state or raises `OpenVpnConfigError` at the validation helper boundary.

## Playwright Boundary

Playwright profile helpers operate on directory trees:

- `playwright_profile_materialize(data_source_path, target_profile_path)` copies `<data-source>/playwright_profile/**` to the pod-local profile path.
- `playwright_profile_snapshot(runtime_profile_path, data_source_path)` copies a runtime profile tree back to `<data-source>/playwright_profile/**`.
- `BrowserRuntime.playwright_runtime_context_get()` materializes the profile and returns the profile path, locale, timezone, and viewport settings for caller-owned Playwright launch code.

The helpers do not know what sites or workflows use the profile. They also do not read domain-specific files inside the tree.

Browser-bound extraction belongs to the caller's Playwright page/context code. Direct HTTP clients are not the primary extraction route for this capability because they bypass browser profile state, browser JavaScript behavior, and the OpenVPN browser-runtime boundary.

`browser-vpn-runtime-playwright-mcp` owns Playwright MCP startup for workflow containers that expose browser tools to Codex. Consumers pass only the mounted DataSource path and pod-local runtime paths. The consumer repository must not configure Codex to execute `@playwright/mcp` directly because package selection, profile materialization, readiness checks, viewport, output directory, and VPN route checks belong to this runtime capability.

## Readiness Contract

`BrowserRuntime.readiness_check()` returns strict Pydantic state with:

- OpenVPN config name,
- persistent profile path,
- locale, timezone, and viewport,
- readiness boolean,
- explicit problem list.

The CLI entrypoint `browser-vpn-runtime-readiness` exposes the same check for container startup probes or smoke tests. `--require-vpn-route` adds a network-namespace check for visible `tun0`; workflows that require an active VPN route may enable it, and local fixture checks may omit it when they only validate secret layout.
