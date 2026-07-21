# Browser VPN Runtime Design

## Purpose

`browser-vpn-runtime` provides one reusable browser automation surface with optional VPN egress. Browser execution, profile state, and Playwright MCP belong to the browser runtime. When enabled, OpenVPN connectivity, SOCKS5 target connections, tunnel lifecycle, and leak prevention belong to the VPN egress gateway. Workflow execution stays external and consumes only the browser MCP service.

## Secret Root Boundaries

The secret root always contains `playwright_profile/`; VPN-enabled mode additionally requires `openvpn/`:

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

When VPN is enabled, the gateway is the only component allowed to mount the whole secret root because `openvpn/config.json` contains credentials. It validates the config name as one local `.ovpn` file inside `openvpn/`, validates the file exists, and writes `login` and `password` to a runtime-owned `0600` authentication file. It does not mutate the source configuration. The browser mounts only the materialized `playwright_profile/` subdirectory; the directory may be empty but is never represented by another source path or fallback field. Direct-egress mode neither requires nor mounts `openvpn/`.

## Gateway Boundary

`browser-vpn-runtime-vpn-egress` creates `/runtime/vpn-egress/` and writes four runtime contracts: Dante configuration, IPv4/IPv6 firewall setup, OpenVPN hooks, and Supervisor configuration. Before replacing the container resolver configuration, it resolves every named OpenVPN `remote` through the bootstrap resolver and pins the returned addresses in the container hosts file. This gives OpenVPN a DNS-independent reconnect path for the lifetime of that container; a container restart refreshes the provider endpoint addresses. The gateway then installs explicit public target DNS endpoints. Dante runs as the constrained proxy user, so its target DNS requests can reach those endpoints only over `tun0`. The gateway installs firewall state and starts Supervisor in the foreground.

The gateway uses Alpine-packaged `openvpn`, `dante-server`, and `supervisor`; it does not implement a proxy itself. Dante listens on `0.0.0.0:1080`, selects `tun0` as its external interface, supports SOCKS5 TCP `connect` requests, and executes ordinary target traffic as the dedicated `vpnproxy` user. Its proxy listener remains unavailable until the OpenVPN up hook starts or restarts it.

The generated IPv4 and IPv6 owner chains apply only to `vpnproxy` output. They allow `ESTABLISHED,RELATED` replies and `NEW` packets whose output interface is `tun0`, then drop every remaining packet. This prevents a proxy target connection from leaking through `eth0` or another non-tunnel route while still allowing the root-owned OpenVPN transport to establish the tunnel.

OpenVPN runs with `--persist-tun` and `--script-security 2`. Its initial up hook starts Dante. Before a reconnect changes tunnel state, the down hook sends `SIGSTOP` to Dante. The process and listening socket remain present, new SOCKS requests wait in the kernel, and the owner-only firewall remains the fail-closed egress boundary while tunnel traffic is unavailable. The next up hook sends `SIGCONT` followed by `SIGHUP`, allowing queued requests to continue after Dante reloads the current `tun0` interface configuration. `persist-tun` may retain interface state for some reconnect paths, but it is not a guarantee that `tun0`, its address, or routes survive a reconnect with changed pushed ifconfig. No browser process depends on that assumption.

## Browser Boundary

The browser launcher accepts an optional `--vpn-proxy-server hostname:port` value. When present, it resolves that hostname immediately before launch, retains the literal IP and port for the launched process, and uses TCP readiness against exactly that endpoint. When omitted, the browser uses direct egress and emits no Playwright proxy configuration. The browser never checks gateway metadata, OpenVPN state, routes, or `tun0`.

In VPN-enabled mode, the generated `@playwright/mcp` `0.0.77` configuration sets `launchOptions.proxy.server` to `socks5://<literal-ip>:<port>` and disables QUIC. Playwright derives the Chromium host-resolver rule from that proxy, rejects local target hostname resolution, and excludes the literal proxy IP so Chromium can still reach the SOCKS endpoint. The runtime must not add a second host-resolver rule because Chromium would let it replace Playwright's proxy exclusion. This keeps target DNS within SOCKS5/Dante and blocks Chromium's UDP/QUIC direct path. The VPN-enabled browser egress policy provides a separate network-level guarantee that the browser pod can reach only the gateway TCP service and cluster DNS. Direct-egress mode omits `launchOptions.proxy` and does not apply that VPN-only egress policy.

The Playwright image has one least-privilege startup boundary. When Docker starts it as root, the container entrypoint creates and assigns only `/output/.playwright-mcp` and `/runtime`, then drops supplementary groups, group identity, and user identity before executing the requested command as `browser`. This permits brand-new bind-mounted output and runtime roots without running Chromium or MCP as root. When the platform already selects the browser UID, the same entrypoint creates accessible directories and executes directly without ownership or identity changes; this preserves the Kubernetes `runAsNonRoot` contract.

An OpenVPN reconnect may still interrupt a target TCP connection that was already in flight. The profile router and internal Playwright MCP processes remain separate from the gateway network namespace and continue to reach it only through SOCKS. For one named backend, `browser_close` disposes the current shared browser context and Chromium process; the next browser tool creates a fresh browser backend. Workflow runtime recovery uses that boundary only after a connection-level navigation failure and a bounded wait, so Chromium does not retain failed-proxy state across the retry. This is browser-context recovery inside one action attempt, not a workflow-step retry.

The public aiohttp router owns one lazy internal Playwright MCP process per active named physical profile and one unprofiled process. A named process keeps `sharedBrowserContext=true` and receives exactly `/runtime/mcp_playwright_profile/profile/<physical-profile>` as `browser.userDataDir`. The unprofiled process sets `browser.isolated=true`, omits `browser.userDataDir`, and does not create a named profile directory. Named config and output paths use `named/<physical-profile>`, while the no-profile backend uses `isolated`, so physical profile names cannot collide with the isolated namespace. Every process has a separate loopback port, config directory, stealth script, and output directory while preserving the same SOCKS, allowed-host, headed Chromium, locale, timezone, and viewport behavior.

The public MCP route accepts optional `profile` and `profile_source` query parameters. Both are parsed as single structural query values and validated as safe physical names. Duplicate values, a source without a target, or equal source and target are rejected. With no explicit source, a named target is copied from the immutable `<secret-root>/playwright_profile` directory only when the target directory is missing; the platform materializes that source directory even when it is empty. With an explicit source, the source must already exist as another run-local physical profile.

Only a new low-level MCP action client session applies `profile_source`, identified exactly as a JSON-RPC `initialize` POST without `mcp-session-id`. Follow-up requests carrying the session header and non-initialization requests do not reset the target. An explicit reset acquires source and target locks in sorted physical-name order, stops the target backend process group, atomically replaces the target from the source, and starts the new backend generation. The reset-triggering initialize response is completely streamed and cleaned up before the sorted source and target locks are released. Ordinary non-reset streams remain outside those lifecycle locks. Operations on different target profiles retain independent locks and may proceed in parallel.

The router strips only `profile` and `profile_source` before forwarding the request. Its aiohttp client has no total timeout for long-lived event streams, and it preserves MCP response status, body streaming, `mcp-session-id`, event-stream content type, and other end-to-end headers. `POST /runtime/mcp-playwright-profile/writeback-candidate?profile=<physical-profile>` requires an empty body. While holding that profile lock, it stops the selected backend process group and atomically replaces the one shared `/runtime/mcp_playwright_profile/writeback_candidate` directory from the selected profile, then returns `204`. Candidate publication never creates a per-profile child: each eligible completion replaces the prior candidate and the last completed publication wins. The next request for that profile starts the same backend lifecycle owner lazily.

`BrowserLocaleConfig` remains the shared source for context locale, HTTP language preference, profile language preferences, and `navigator.languages` override. Its defaults remain generic: `en-US` and `UTC`.

## Kubernetes Reference

The reference manifest demonstrates VPN-enabled mode with two Deployment/Service pairs:

- `vpn-egress` mounts the full secret root and `/dev/net/tun`, runs as root with only `NET_ADMIN`, and exposes TCP `1080`.
- `browser-mcp` runs only the public profile router on TCP `8931`, mounts immutable profile input through a `playwright_profile` subPath, mounts the writeback PVC at `/runtime/mcp_playwright_profile` and the runtime-profile PVC at its `/profile` child, has no tunnel device or `NET_ADMIN`, and receives `vpn-egress:1080` as its only proxy endpoint. The shared candidate and its temporary sibling therefore reside on the same writeback filesystem and the candidate path itself is not a mountpoint.

The reference requires the source PVC to contain `playwright_profile/`; an empty directory represents the first-run profile. This is a Kubernetes `subPath` prerequisite, not a browser runtime requirement for direct non-Kubernetes use.

`browser-mcp-egress-deny` permits only TCP `1080` to gateway pods and TCP/UDP `53` to kube-dns. The workflow executor remains in a separate pod or runtime and connects to the browser MCP Service. It does not join either network namespace and it does not receive OpenVPN credentials.

## Verification Contract

Behavior tests cover strict OpenVPN input validation, generated gateway files and firewall policy, literal proxy configuration, backend-owned SOCKS5 TCP reachability, profile reset and candidate publication, MCP proxy streaming, and Kubernetes resource relationships. Platform TCP healthchecks own router readiness; internal backends start lazily and perform their own loopback readiness check. Container integration still requires a cluster or local container runtime with an actual `/dev/net/tun`, valid OpenVPN credentials, and a reachable VPN endpoint; unit tests cannot prove provider-side tunnel establishment or Internet egress.
