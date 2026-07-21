# Browser VPN Runtime

`browser-vpn-runtime` is a reusable browser capability with optional OpenVPN egress. It owns browser profile preparation, runtime-owned Playwright MCP startup, and the separately deployable VPN egress gateway used only when requested. It does not own workflow orchestration, extraction behavior, or domain-specific logic.

## Runtime Layout

A secret root always contains a materialized browser profile directory. VPN-enabled deployments additionally contain the gateway-owned OpenVPN input:

```text
<secret-root>/
  openvpn/
    config.json
    <name>.ovpn
  playwright_profile/
    ...
  codex_profile/
    ...
```

`openvpn/config.json` has the strict form:

```json
{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}
```

The config name is one local `.ovpn` file name. It cannot contain `/`, `..`, or an absolute path, and it must exist below `openvpn/`. The gateway validates this input and writes a mode-`0600` `--auth-user-pass` file in its writable runtime directory. Browser pods mount only `playwright_profile/`; they do not receive the OpenVPN secret tree.

## VPN Egress Gateway

`browser-vpn-runtime-vpn-egress` runs OpenVPN and Alpine's packaged Dante `sockd` under Alpine's packaged `supervisord`. It writes the Dante, supervisor, firewall, and OpenVPN hook files into `/runtime/vpn-egress/`, installs its firewall before the supervisor starts, and never rewrites the secret root `.ovpn` file. Before installing tunnel-only target DNS, the gateway resolves every named OpenVPN `remote` through the container's bootstrap resolver and pins the resulting addresses in the container hosts file. OpenVPN reconnects therefore do not depend on DNS routed through the tunnel. A container restart resolves the provider endpoints again. Dante's unprivileged target DNS traffic can reach the configured public resolvers only through `tun0`.

Dante listens on TCP `1080` and runs target traffic as the unprivileged `vpnproxy` user. Its generated IPv4 and IPv6 output chains allow only established replies and new `vpnproxy` traffic with output interface `tun0`; every other `vpnproxy` output packet is dropped. The gateway can still establish OpenVPN as root, but Dante cannot fall back to `eth0` when the tunnel disappears.

OpenVPN `up` and `down` hooks control Dante through Supervisor. Initial tunnel readiness starts Dante. Before reconnect changes tunnel state, the down hook sends `SIGSTOP`: Dante retains its stable listening socket and the kernel queues new SOCKS requests instead of returning a false terminal proxy error. Its owner-only firewall still blocks any fallback outside `tun0`. The next up event sends `SIGCONT` and `SIGHUP`, so queued requests resume against the current tunnel configuration without replacing the listener process. `--persist-tun` remains enabled as a reconnect optimization, but it does not guarantee preservation of `tun0`, its address, or its routes when the server pushes changed interface configuration. Browser processes are insulated from that lifecycle because they never share the gateway network namespace.

Run the gateway directly:

```bash
browser-vpn-runtime-vpn-egress \
  --secret-root-path /input/.secret \
  --runtime-path /runtime
```

## Playwright MCP

`browser-vpn-runtime-playwright-mcp-router` exposes one public MCP endpoint and lazily starts one internal `@playwright/mcp` process per active named physical profile. It also owns one unprofiled backend configured with `browser.isolated=true` and no `browser.userDataDir`. `--vpn-proxy-server hostname:port` is optional. When present, every internal backend resolves the endpoint once to a literal IP address, waits for that IP and port to accept TCP, and configures Chromium with the SOCKS5 proxy. When omitted, Chromium uses direct egress and the generated launch configuration contains no proxy field. Both modes write the same disjoint `@playwright/mcp` `0.0.77` config, stealth script, and output path. VPN-enabled mode additionally guarantees:

- a `socks5://<literal-ip>:<port>` Playwright launch proxy;
- `--disable-quic`;
- Playwright-owned Chromium host-resolver rules that keep target DNS out of the browser pod while excluding the literal proxy IP;
- no direct-proxy fallback configuration.

The launcher must not add another `--host-resolver-rules` argument. Chromium accepts only one effective rule, and a later generic rule would replace Playwright's proxy-IP exclusion and make the configured SOCKS endpoint unreachable.

SOCKS5 target hostname resolution stays at the gateway side. Each Playwright MCP backend resolves the configured gateway once and waits for that exact SOCKS5 TCP endpoint. The runtime launches each headed backend in its own process group and waits for that complete group to exit before resetting or snapshotting a profile. Platform TCP healthchecks own router readiness; there is no separate runtime readiness command.

The Playwright image entrypoint prepares only its fixed writable roots, `/output/.playwright-mcp` and `/runtime`, before browser startup. Under ordinary Docker bind mounts it starts with root privileges solely to create and assign those roots, then replaces itself with the requested command as the unprivileged `browser` user. When an orchestrator already starts the image as that browser UID, as in the Kubernetes reference, it creates accessible roots without changing ownership or identity. Browser and MCP processes never run as root.

An already active target connection can still fail while OpenVPN reconnects. In that case the workflow runtime keeps the error in its browser result, waits for gateway recovery, calls the standard Playwright MCP `browser_close` tool, and reopens the same target. `browser_close` disposes the current browser backend while the MCP server stays available; the next browser tool starts a fresh Chromium network context without restarting the workflow step. Each named backend preserves one shared browser context, so callers must not concurrently operate the same physical profile. Different named profiles use different processes and may run in parallel.

Public MCP requests select profiles structurally:

- `?profile=<physical-profile>` reuses one named backend and its `/runtime/mcp_playwright_profile/profile/<physical-profile>` directory.
- `?profile=<target>&profile_source=<source>` atomically resets the target from a run-local physical source only for a new MCP `initialize` POST without `mcp-session-id`. Follow-up requests in that session do not reset it.
- no profile query uses the single unprofiled isolated-session backend and creates no named profile directory.

A named target without `profile_source` is copied from the immutable `<secret-root>/playwright_profile` directory only when the target does not yet exist. The source directory is always materialized by the platform, and may be empty. Unsafe or duplicate profile values, a source without a target, equal source and target, and a missing explicit source are rejected.

```bash
browser-vpn-runtime-playwright-mcp-router \
  --secret-root-path /input/.secret \
  --profile-root-path /runtime/mcp_playwright_profile/profile \
  --candidate-root-path /runtime/mcp_playwright_profile/writeback_candidate \
  --output-root-path /output/.playwright-mcp \
  --backend-runtime-root-path /runtime/playwright_mcp_backend \
  --host 0.0.0.0 \
  --allowed-hosts localhost,127.0.0.1,browser-mcp \
  --port 8931 \
  --vpn-proxy-server vpn-egress:1080
```

The router streams MCP responses without a total proxy timeout and preserves status, session, and event-stream headers while removing only its own profile query parameters before proxying. `POST /runtime/mcp-playwright-profile/writeback-candidate?profile=<physical-profile>` accepts an empty body, stops that profile's backend, and atomically replaces the one shared candidate directory at `/runtime/mcp_playwright_profile/writeback_candidate` with `204`. Each eligible completion replaces the prior candidate, so the last completed publication wins. A later MCP request restarts the stopped backend lazily.

Backend output directories stay inside the runtime-owned `.playwright-mcp` artifact namespace. Named backend config and output paths use `named/<physical-profile>`, while the no-profile backend uses `isolated`; a named profile literally called `unprofiled` therefore cannot collide with the isolated backend. Generated MCP configuration, stealth scripts, outputs, profiles, and internal ports are separate for every backend. `BrowserLocaleConfig` remains the single typed owner of browser locale, HTTP language header, Chromium preferences, and stealth navigator language values. Its neutral defaults are `en-US` and `UTC`.

## Kubernetes Reference

`deploy/k8s/runtime-capability.yaml` declares independent `vpn-egress` and `browser-mcp` Deployments and matching Services. Only `vpn-egress` gets `NET_ADMIN`, `/dev/net/tun`, and the full read-only secret root. `browser-mcp` has no tunnel device or network-administration capability and receives only the immutable source profile subdirectory. The writeback PVC owns `/runtime/mcp_playwright_profile`, while the run-local profile PVC is mounted at its `/profile` child; candidate staging and atomic replacement therefore stay on the writeback filesystem. Its egress NetworkPolicy permits TCP `1080` to the gateway and TCP/UDP `53` to kube-dns; direct target traffic is denied.

The source PVC used by this reference must contain `playwright_profile/`; an empty directory is valid for a first-run profile. Kubernetes requires the source of this read-only `subPath` mount to exist before the browser pod starts.

Workflow execution remains external to both pods. It connects to `http://browser-mcp:8931/mcp` and must not launch `@playwright/mcp`, `npx`, or an independent browser stack.

## Development

```bash
python -m pip install -e ".[browser,test]"
.venv/bin/pytest -q
python -m compileall browser_vpn_runtime
```
