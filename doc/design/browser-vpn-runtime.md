# Browser VPN Runtime Design

## Purpose

`browser-vpn-runtime` is a reusable runtime capability for workflows that need browser automation over an OpenVPN connection. It owns runtime contracts, secret layout validation, and Playwright profile directory movement. It does not own domain extraction, domain schemas, domain logic, or workflow orchestration policy.

## Secret Layout

The private DataSource mount has three conventional responsibilities and is mounted read-only at `/input/.secret` in container runtimes:

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

`openvpn/config.json` is optional for generic runtime consumers. When it exists, OpenVPN is enabled, the file selects the OpenVPN file through `openvpn_config_name`, and it stores `login` plus `password` for `--auth-user-pass`. The config name is one local `.ovpn` file name. It must not contain `/`, must not contain `..`, must not be an absolute path, and must resolve to an existing file inside `openvpn/`. When `openvpn/config.json` does not exist, browser runtime may run without VPN only if the caller has not declared OpenVPN mandatory. The Playwright MCP launcher flag `--require-openvpn` turns absent metadata into a startup error.

`playwright_profile/**` is an optional directory tree copied into the pod-local runtime directory. When it does not exist, browser runtime starts with an empty profile. Zip-based profile import/export is intentionally outside this contract because browser profile state is already a filesystem tree and Kubernetes volumes can mount it directly. Profile writeback snapshots the runtime profile into a temp directory next to the target, applies the target host owner to that temp tree, and then atomically replaces the target tree. Ownership and permissions must not be changed after the replace.

`codex_profile/**` is an optional reserved sibling tree for agent or Codex runtime state. This package does not inspect it, so callers can evolve that profile independently.

## Kubernetes Boundary

The reference Kubernetes shape has two separate runtime units:

- `workflow-runtime`: the workflow executor container or pod that runs DBOS, Codex, package installation, ordinary network calls, and non-browser work through the normal cluster network.
- `browser-runtime`: a dedicated pod or service for browser traffic; it contains an `openvpn` container and one `playwright-mcp` container that share that pod network namespace.

The workflow runtime must not run in the OpenVPN network namespace. OpenVPN route changes must affect only the browser runtime pod, because Codex itself, dependency installation, DBOS, and other non-browser network calls must use the normal network path. Codex receives only the HTTP MCP URL of the browser runtime service and uses that MCP server when a stage needs browser access.

The `openvpn` container owns VPN process startup and tunnel lifecycle. The `playwright-mcp` container owns Playwright MCP startup, browser launch, browser profile materialization, stealth setup, locale, timezone, viewport, and browser artifact output. These two containers share a network namespace so only the browser process exits through the VPN tunnel. The DataSource secret volume is mounted read-only at `/input/.secret`, and `/runtime` is writable pod-local state. Workflow containers that need secret contents copy `/input/.secret` to `/runtime/.secret` at startup and use only that runtime copy. The `openvpn` sidecar creates `/runtime/openvpn-auth.txt` from `openvpn/config.json`, sets mode `0600`, and passes it to OpenVPN with `--auth-user-pass`.

This repository provides only reference runtime images and contracts. Production consumers should set their own pod shape, service names, resource limits, probes, and secret names while preserving the network-boundary rule above.

## OpenVPN Boundary

OpenVPN config validation has two layers:

- `BrowserRuntimeConfig` validates the user-facing `openvpn_config_name` syntax when it is not empty.
- `openvpn_config_validate(...)` reads `openvpn/config.json`, validates the same syntax, and checks that the named file exists under `openvpn/`.
- `openvpn_auth_file_write(...)` writes the runtime `--auth-user-pass` file into pod-local writable storage without mutating the private DataSource mount.

The runtime never accepts fallback names, alternate paths, absolute paths, or path traversal. Absent `openvpn/config.json` means no-VPN runtime. Invalid present config returns a not-ready runtime state or raises `OpenVpnConfigError` at the validation helper boundary.

## Playwright Boundary

Playwright profile helpers operate on directory trees:

- `playwright_profile_materialize(data_source_path, target_profile_path)` copies `<data-source>/playwright_profile/**` to the pod-local profile path only when the target path does not already exist.
- `playwright_profile_snapshot(runtime_profile_path, data_source_path)` copies a runtime profile tree into a sibling temp tree, applies requested ownership to that temp tree, and atomically replaces `<data-source>/playwright_profile/**`.
- `BrowserRuntime.playwright_runtime_context_get()` materializes the profile and returns the profile path, locale, timezone, and viewport settings for caller-owned Playwright launch code.

The helpers do not know what sites or workflows use the profile. They also do not read domain-specific files inside the tree.

Browser-bound extraction belongs to the caller's Playwright page/context code. Direct HTTP clients are not the primary extraction route for this capability because they bypass browser profile state, browser JavaScript behavior, and the OpenVPN browser-runtime boundary.

`browser-vpn-runtime-playwright-mcp` owns Playwright MCP startup for workflow projects that expose browser tools to Codex. Consumers start one launcher process per workflow run inside the browser runtime pod and configure Codex in the workflow runtime with that process HTTP MCP URL. Consumers pass only the mounted DataSource path, pod-local runtime paths, caller-owned writable `.playwright-mcp` artifact namespace, bind host, allowed MCP host names, port, and the mandatory OpenVPN flag. When the workflow runtime connects through a Kubernetes Service or Docker Compose service name, that host name must be present in `--allowed-hosts`; otherwise `@playwright/mcp` rejects the connection before Codex can use the browser. The consumer repository must not configure Codex to execute `@playwright/mcp`, `npx`, or another Playwright MCP launcher directly because package selection, profile materialization, readiness checks, stealth initialization, locale, timezone, viewport, output directory, and VPN route checks belong to this runtime capability.

`--output-dir` is the caller-owned writable `.playwright-mcp` artifact namespace used by Playwright MCP file output, for example `/output/.playwright-mcp/current`. Passing the workflow output root itself is forbidden because Playwright MCP writes automatic page and console artifacts directly under `outputDir`. Browser tools can write workflow evidence only under the configured MCP `outputDir` or `/app`, so consumers that need shared evidence artifacts must pass a subdirectory inside their shared output root as `--output-dir`. `--mcp-config-path` and `--persistent-profile-path` remain pod-local runtime-scoped paths; generated `config.json`, the `playwright_stealth` init script, and the mutable browser profile must not be placed under the shared evidence root only because `--output-dir` points there.

The launcher uses one browser stack: headed Chromium through `xvfb-run`, persistent `userDataDir`, `sharedBrowserContext`, `playwright_stealth` init script, Turkish locale, Istanbul timezone, 1920x1080 viewport by default, and file-backed MCP artifacts under the caller-owned `.playwright-mcp` namespace. Headless fallback, per-stage profile directories, direct `npx` execution, and caller-owned browser flags are outside this runtime contract.

## Readiness Contract

`BrowserRuntime.readiness_check()` returns strict Pydantic state with:

- OpenVPN config name,
- persistent profile path,
- locale, timezone, and viewport,
- readiness boolean,
- explicit problem list.

The CLI entrypoint `browser-vpn-runtime-readiness` exposes the same check for container startup probes or smoke tests. `--require-vpn-route` adds a network-namespace check for visible `tun0`; workflows that require an active VPN route may enable it, and local fixture checks may omit it when they only validate secret layout.
