# browser-vpn-runtime

Reusable runtime capability for browser automation through an OpenVPN tunnel. This repository intentionally contains no marketplace-specific extraction logic; application workflows bring their own Playwright page/context behavior and use this package only for runtime configuration, profile directory handling, and readiness checks.

## Secret Layout

The runtime expects one private DataSource directory mounted into the pod. The conventional tree is:

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

`openvpn/config.json` must contain:

```json
{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}
```

`openvpn_config_name` is validated as one local file name: it must not contain `/`, must not contain `..`, must not be absolute, and must name an existing file under `openvpn/`. The OpenVPN sidecar writes `login` and `password` into pod-local `/runtime/openvpn-auth.txt` and launches OpenVPN with `--auth-user-pass`; the secret volume remains read-only.

`playwright_profile/**` is a directory tree, not a zip archive. The helper `playwright_profile_materialize(...)` copies that tree into a pod-local persistent profile directory before browser launch. The helper `playwright_profile_snapshot(...)` copies the runtime tree back to `playwright_profile/**` when the caller chooses to persist browser state.

`codex_profile/**` is reserved for callers that need a separate Codex or agent profile. This package documents the path but does not interpret its contents.

## Kubernetes Boundary

The reference deployment in `deploy/k8s/runtime-capability.yaml` uses one pod with:

- an `openvpn` sidecar that reads `/data-source/openvpn/config.json` and launches the named `.ovpn` file,
- an application/browser container in the same pod network namespace, so browser traffic exits through the VPN tunnel,
- one secret volume mounted read-only at `/data-source`,
- one writable `emptyDir` mounted at `/runtime` for the pod-local Playwright profile.

Kubernetes gives containers in one pod a shared network namespace. The application container must not create a second VPN tunnel or bypass the sidecar with direct host networking.

Workflow containers that must fail when the VPN tunnel is not visible should run readiness with `--require-vpn-route`. That check verifies that `tun0` exists in the current network namespace and reports an explicit readiness problem when it does not.

## Playwright Boundary

Browser-bound extraction should go through Playwright browser contexts and pages. Direct HTTP is not the primary runtime path because it bypasses browser profile state, JavaScript execution, and the VPN/browser boundary this capability exists to provide.

The package does not import Playwright directly in its readiness path. Application containers install Playwright runtime dependencies and launch browser automation through this package.

`BrowserRuntime.playwright_runtime_context_get()` materializes `playwright_profile/**` into the pod-local persistent profile path and returns the profile path, locale, timezone, viewport width, and viewport height for caller-owned Playwright launch code.

`browser-vpn-runtime-playwright-mcp` is the runtime-owned entrypoint for Playwright MCP. Workflow projects configure Codex MCP to execute this command or `python -m browser_vpn_runtime.playwright_mcp`; workflow projects must not invoke `@playwright/mcp` directly. The launcher validates the mounted `secret`, materializes `playwright_profile/**`, configures the profile path, viewport, output directory, and replaces itself with the Playwright MCP server process.

## Development

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run tests:

```bash
python -m pytest -q
```

Run readiness check:

```bash
browser-vpn-runtime-readiness \
  --data-source-path /data-source \
  --openvpn-config-name client.ovpn \
  --persistent-profile-path /runtime/playwright_profile \
  --require-vpn-route
```

The command prints strict JSON readiness state. Without `--require-vpn-route`, it validates mounted OpenVPN metadata and the named `.ovpn` file. With `--require-vpn-route`, it exits with `0` only when `tun0` is also visible in the process network namespace.

Launch Playwright MCP through this runtime:

```bash
browser-vpn-runtime-playwright-mcp \
  --data-source-path /data-source \
  --persistent-profile-path /runtime/playwright_profile \
  --output-dir /runtime/playwright_mcp \
  --require-vpn-route
```
