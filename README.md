# browser-vpn-runtime

Reusable runtime capability for browser automation through an OpenVPN tunnel. This repository intentionally contains no marketplace-specific extraction logic; application workflows bring their own Playwright page/context behavior and use this package only for runtime configuration, profile directory handling, and readiness checks.

## Secret Layout

The runtime expects one private DataSource directory mounted read-only into the pod. The conventional mount path is `/input/.secret`; examples below use `<data-source>` for any caller-provided path. The conventional tree is:

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

`openvpn/config.json` is optional for generic runtime consumers. When it exists, browser runtime validates it and treats OpenVPN as enabled. When the Playwright MCP launcher runs with `--require-openvpn`, absence of this file is a startup error. When present, `openvpn/config.json` must contain:

```json
{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}
```

`openvpn_config_name` is validated as one local file name: it must not contain `/`, must not contain `..`, must not be absolute, and must name an existing file under `openvpn/`. The OpenVPN sidecar is used only by VPN-enabled deployments. It writes `login` and `password` into pod-local `/runtime/openvpn-auth.txt` and launches OpenVPN with `--auth-user-pass`; the secret volume remains read-only.

`playwright_profile/**` is an optional directory tree, not a zip archive. When it exists, the helper `playwright_profile_materialize(...)` copies that tree into a pod-local persistent profile directory before browser launch. When it does not exist, the runtime starts from an empty browser profile. The helper `playwright_profile_snapshot(...)` writes a snapshot into a sibling temp directory, applies the target host owner to that temp tree, and then atomically replaces `playwright_profile/**`; it must not change permissions or ownership after publish.

`codex_profile/**` is optional and reserved for callers that need a separate Codex or agent profile. This package documents the path but does not interpret its contents.

## Kubernetes Boundary

The reference deployment has two separate runtime units:

- a workflow runtime container or pod that runs DBOS, Codex, dependency installation, and ordinary network calls through the normal network,
- a browser runtime pod or service that contains `openvpn` plus `playwright-mcp` in the same network namespace, so only browser traffic exits through the VPN tunnel.

The workflow runtime must not run in the OpenVPN network namespace. Codex receives the browser runtime HTTP MCP URL and uses that server only for browser actions. Runtime pods mount the private DataSource read-only at `/input/.secret` and mount writable pod-local storage at `/runtime`. Workflow containers that need secret contents copy `/input/.secret` to `/runtime/.secret` at startup and use only that runtime copy.

Workflow containers that require browser access must fail when the configured browser runtime MCP URL is missing. Browser runtime containers that require the VPN tunnel should run readiness with `--require-vpn-route`; that check verifies that `tun0` exists in the browser runtime network namespace and reports an explicit readiness problem when it does not.

## Playwright Boundary

Browser-bound extraction should go through Playwright browser contexts and pages. Direct HTTP is not the primary runtime path because it bypasses browser profile state, JavaScript execution, and the VPN/browser boundary this capability exists to provide.

The package does not import Playwright directly in its readiness path. Browser runtime containers install Playwright runtime dependencies and expose browser automation through this package.

`BrowserRuntime.playwright_runtime_context_get()` materializes `playwright_profile/**` into the pod-local persistent profile path and returns the profile path, locale, timezone, viewport width, and viewport height for caller-owned Playwright launch code. If the target profile path already exists, the helper returns it unchanged so one workflow run keeps one mutable browser profile instead of overwriting it at every stage.

`browser-vpn-runtime-playwright-mcp` is the runtime-owned entrypoint for Playwright MCP. Workflow projects start this entrypoint once for one workflow run inside the browser runtime pod or service and connect Codex in the workflow runtime to its HTTP MCP URL. Workflow projects must not invoke `@playwright/mcp`, `npx`, or another Playwright MCP launcher directly. The launcher validates the mounted `secret`, materializes `playwright_profile/**`, writes a runtime-owned Playwright MCP config, writes a `playwright_stealth` init script, forces headed Chromium through `xvfb-run`, configures Turkish locale and Istanbul timezone, and replaces itself with the Playwright MCP server process.

`--output-dir` must point at a caller-owned writable `.playwright-mcp` artifact namespace, for example `/output/.playwright-mcp/current`. Passing a workflow output root such as `/output` is forbidden because Playwright MCP writes automatic page and console artifacts directly under `outputDir`. This path is intentionally separate from runtime-scoped paths: `--mcp-config-path` should stay under pod-local runtime storage for the generated MCP config, and `--persistent-profile-path` should stay under pod-local runtime storage for the mutable browser profile. This separation lets browser tools write evidence under the caller's shared output root while keeping generated runtime files out of root workflow artifact directories.

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
  --data-source-path /input/.secret \
  --openvpn-config-name client.ovpn \
  --persistent-profile-path /runtime/playwright_profile \
  --require-vpn-route
```

The command prints strict JSON readiness state. Without `openvpn/config.json`, readiness reports a no-VPN runtime. With `openvpn/config.json`, it validates mounted OpenVPN metadata and the named `.ovpn` file. With `--require-vpn-route`, it exits with `0` only when `tun0` is also visible in the process network namespace.

Launch Playwright MCP through this runtime:

```bash
browser-vpn-runtime-playwright-mcp \
  --data-source-path /input/.secret \
  --persistent-profile-path /runtime/playwright_profile \
  --output-dir /output/.playwright-mcp/current \
  --mcp-config-path /runtime/playwright_mcp/config.json \
  --host 0.0.0.0 \
  --allowed-hosts localhost,127.0.0.1,browser-runtime \
  --port 8931 \
  --require-openvpn
```
