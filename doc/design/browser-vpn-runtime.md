# Browser VPN Runtime Design

## Purpose

`browser-vpn-runtime` is a reusable runtime capability for workflows that need browser automation over an OpenVPN connection. It owns runtime contracts, secret layout validation, and Playwright profile directory movement. It does not own domain extraction, domain schemas, domain logic, or workflow orchestration policy.

Shared workflow-container ecosystem authoring and code quality rules live in the `workflow-container-developer` plugin reference `references/workflow-container-authoring.md`; this document owns only browser/VPN runtime-specific boundaries.

## DataSource Layout

The private DataSource filesystem has three conventional responsibilities and is mounted read-only at `/input/.secret` in container runtimes:

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

`playwright_profile/**` is an optional directory tree copied into the pod-local runtime directory. When it does not exist, browser runtime starts with an empty profile. Zip-based profile import/export is intentionally outside this contract because browser profile state is already a filesystem tree and Kubernetes volumes can mount it directly.

Profile writeback targets a separate writable root, never the read-only DataSource mount. It copies the runtime profile into a sibling temp directory and applies the requested owner before publication. An absent target is published with one `os.replace(...)`. An existing non-empty target is published in a Linux container with one `renameat2(RENAME_EXCHANGE)`, after which the old tree is removed from the former temp path and the parent directory is fsynced. This preserves a target directory at every namespace-visible instant. Existing-target writeback requires Linux and filesystem support for `RENAME_EXCHANGE`; there is no two-rename fallback because that would reintroduce a target-absence window. Ownership and permissions must not change after publication.

`codex_profile/**` is an optional reserved sibling tree for agent or Codex runtime state. This package does not inspect it, so callers can evolve that profile independently.

## Kubernetes Boundary

The reference Kubernetes shape has two separate runtime units:

- `workflow-runtime`: the workflow executor container or pod that runs DBOS, Codex, package installation, ordinary network calls, and non-browser work through the normal cluster network.
- `browser-runtime`: a dedicated pod or service for browser traffic; it contains an `openvpn` container and one `playwright-mcp` container that share that pod network namespace.

The workflow runtime must not run in the OpenVPN network namespace. OpenVPN route changes must affect only the browser runtime pod, because Codex itself, dependency installation, DBOS, and other non-browser network calls must use the normal network path. Codex receives only the HTTP MCP URL of the browser runtime service and uses that MCP server when a step needs browser access.

The `openvpn` container owns VPN process startup and tunnel lifecycle. The `browser-runtime` container launches the real `browser-vpn-runtime-playwright-mcp` executable and owns browser launch, profile materialization, stealth setup, locale, timezone, viewport, and artifact output. These two containers share only the browser pod network namespace, so workflow execution remains on the normal cluster network. The external DataSource claim is mounted read-only at `/input/.secret`, `/runtime` is writable pod-local state, `/dev/net/tun` is mounted only into OpenVPN, `/runtime-profile` is a mutable profile claim, and `/output` is a shared external artifact volume. The `openvpn` sidecar creates `/runtime/openvpn-auth.txt` from `openvpn/config.json`, sets mode `0600`, and passes it to OpenVPN with `--auth-user-pass`.

`deploy/k8s/runtime-capability.yaml` provides one-replica `Deployment`, TCP readiness on MCP port `8931`, a `ClusterIP` Service named `browser-vpn-runtime`, and separate claims for browser artifacts, mutable runtime profile, and profile writeback. The input DataSource claim is externally provisioned and is never declared writable. A generic filesystem claim is used instead of a Kubernetes Secret because the DataSource may contain a large arbitrary browser-profile tree that does not fit the Secret object contract. A separate workflow pod connects to `http://browser-vpn-runtime:8931/mcp` and may mount the same external artifact claim. It must not be added to the browser pod.

`deploy/k8s/profile-writeback-job.yaml` is the executable production writeback boundary and is suspended by default. The caller first scales the browser Deployment to zero and waits for its pod to disappear, applies a fresh Job, and explicitly clears `spec.suspend`. The Job then mounts the runtime-profile claim read-only, invokes `browser-vpn-runtime-playwright-profile-snapshot`, and publishes into the writable writeback claim. It publishes as its process owner by default; a platform that requires another UID/GID adds the CLI owner arguments before unsuspending the Job. Ownership changes after publication are forbidden. Running the Job beside a live browser replica is forbidden because Chromium may still mutate the source profile. The platform consumes the completed writeback claim and owns synchronization into its DataSource implementation. Production consumers may specialize images, resources, storage classes, claim names, and owner IDs while preserving these network, mount, shutdown, and atomic-publication boundaries.

## OpenVPN Boundary

OpenVPN config validation has two layers:

- `BrowserRuntimeConfig` validates the user-facing `openvpn_config_name` syntax when it is not empty.
- `openvpn_config_validate(...)` reads `openvpn/config.json`, validates the same syntax, and checks that the named file exists under `openvpn/`.
- `openvpn_auth_file_write(...)` writes the runtime `--auth-user-pass` file into pod-local writable storage without mutating the private DataSource mount.

The runtime never accepts fallback names, alternate paths, absolute paths, or path traversal. Absent `openvpn/config.json` means no-VPN runtime. Invalid present config returns a not-ready runtime state or raises `OpenVpnConfigError` at the validation helper boundary.

The DataSource-owned `.ovpn` file selects remote endpoints and transports. The runtime does not rewrite that file or choose UDP versus TCP for the caller. A consumer that requires TCP-only operation must provide a config containing only TCP remotes.

The OpenVPN sidecar always adds `--persist-tun` to the validated config invocation. This runtime-owned stability policy keeps the TUN interface and its route configuration across `SIGUSR1` and `--ping-restart` reconnects, so a browser in the shared network namespace does not observe a route teardown and interface recreation as `ERR_NETWORK_CHANGED`. A prolonged or terminal remote outage may still produce a browser timeout or another structured network failure; `persist-tun` does not convert an unavailable VPN endpoint into a successful request.

Reconnect verification must run repeated MCP browser navigations while forcing an OpenVPN `SIGUSR1` restart. The verification passes only when the `tun0` interface index remains stable, OpenVPN does not close and recreate the TUN interface or its routes, and no MCP navigation response contains `ERR_NETWORK_CHANGED`. A separate MCP retry proxy is not part of the runtime while this lower-level invariant holds; it becomes justified only if the same verification still exposes a transient navigation failure after tunnel persistence is active.

## Playwright Boundary

Playwright profile helpers operate on directory trees:

- `playwright_profile_materialize(data_source_path, target_profile_path)` copies `<data-source>/playwright_profile/**` to the pod-local profile path only when the target path does not already exist.
- `playwright_profile_snapshot(runtime_profile_path, writeback_root_path)` copies a runtime profile tree into a sibling temp tree, applies requested ownership, and publishes `<writeback-root>/playwright_profile/**` with the atomic Linux contract above.
- `browser-vpn-runtime-playwright-profile-snapshot` exposes the same domain-neutral writeback boundary to production callers and returns the published state as JSON.
- `BrowserRuntime.playwright_runtime_context_get()` materializes the profile and returns the profile path, typed locale configuration, timezone, and viewport settings for caller-owned Playwright launch code.

The helpers do not know what sites or workflows use the profile. They also do not read domain-specific files inside the tree.

Browser-bound extraction belongs to the caller's Playwright page/context code. Direct HTTP clients are not the primary extraction route for this capability because they bypass browser profile state, browser JavaScript behavior, and the OpenVPN browser-runtime boundary.

`browser-vpn-runtime-playwright-mcp` owns Playwright MCP startup for workflow projects that expose browser tools to Codex. Consumers start one launcher process per workflow run inside the browser runtime pod and configure Codex in the workflow runtime with that process HTTP MCP URL. Consumers pass only the mounted DataSource path, pod-local runtime paths, caller-owned writable `.playwright-mcp` artifact namespace, bind host, allowed MCP host names, port, and the mandatory OpenVPN flag. When the workflow runtime connects through a Kubernetes Service or Docker Compose service name, that host name must be present in `--allowed-hosts`; otherwise `@playwright/mcp` rejects the connection before Codex can use the browser. The consumer repository must not configure Codex to execute `@playwright/mcp`, `npx`, or another Playwright MCP launcher directly because package selection, profile materialization, readiness checks, stealth initialization, locale, timezone, viewport, output directory, and VPN route checks belong to this runtime capability.

`--output-dir` is the caller-owned writable `.playwright-mcp` artifact namespace used by Playwright MCP file output, for example `/output/.playwright-mcp/current`. Passing the workflow output root itself is forbidden because Playwright MCP writes automatic page and console artifacts directly under `outputDir`. Browser tools can write workflow evidence only under the configured MCP `outputDir` or `/app`, so consumers that need shared evidence artifacts must pass a subdirectory inside their shared output root as `--output-dir`. `--mcp-config-path` and `--persistent-profile-path` remain pod-local runtime-scoped paths; generated `config.json`, the `playwright_stealth` init script, and the mutable browser profile must not be placed under the shared evidence root only because `--output-dir` points there.

The launcher uses one browser stack: headed Chromium through `xvfb-run`, persistent `userDataDir`, `sharedBrowserContext`, `playwright_stealth` init script, 1920x1080 viewport by default, and file-backed MCP artifacts under the caller-owned `.playwright-mcp` namespace. `BrowserLocaleConfig` is the single typed owner of the browser locale and derives the HTTP, Chromium preference, and stealth navigator language forms. Its neutral default is `en-US`; consumers pass another locale explicitly, and browser defaults do not infer locale from caller domain data. Timezone is independently configurable and defaults to `UTC` because locale does not determine timezone. Headless fallback, per-step profile directories, direct `npx` execution, and caller-owned browser flags are outside this runtime contract.

## Readiness Contract

`BrowserRuntime.readiness_check()` returns strict Pydantic state with:

- OpenVPN config name,
- persistent profile path,
- typed locale configuration, timezone, and viewport,
- readiness boolean,
- explicit problem list.

The CLI entrypoint `browser-vpn-runtime-readiness` exposes the same check for container startup probes or smoke tests. `--require-vpn-route` adds a network-namespace check for visible `tun0`; workflows that require an active VPN route may enable it, and local fixture checks may omit it when they only validate secret layout.
