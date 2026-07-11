# Browser VPN Runtime Design

## Purpose

`browser-vpn-runtime` provides one reusable browser automation surface with a stable VPN egress boundary. Browser execution, profile state, and Playwright MCP belong to the browser runtime. OpenVPN connectivity, SOCKS5 target connections, tunnel lifecycle, and leak prevention belong to the VPN egress gateway. Workflow execution stays external and consumes only the browser MCP service.

## DataSource Boundaries

The DataSource layout is:

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

The gateway is the only component allowed to mount the whole DataSource because `openvpn/config.json` contains credentials. It validates the config name as one local `.ovpn` file inside `openvpn/`, validates the file exists, and writes `login` and `password` to a runtime-owned `0600` authentication file. It does not mutate the source configuration. The browser mounts only the `playwright_profile/` subdirectory and treats an absent profile as a first-run empty profile.

## Gateway Boundary

`browser-vpn-runtime-vpn-egress` creates `/runtime/vpn-egress/` and writes four runtime contracts: Dante configuration, IPv4/IPv6 firewall setup, OpenVPN hooks, and Supervisor configuration. Before replacing the container resolver configuration, it resolves every named OpenVPN `remote` through the bootstrap resolver and pins the returned addresses in the container hosts file. This gives OpenVPN a DNS-independent reconnect path for the lifetime of that container; a container restart refreshes the provider endpoint addresses. The gateway then installs explicit public target DNS endpoints. Dante runs as the constrained proxy user, so its target DNS requests can reach those endpoints only over `tun0`. The gateway installs firewall state and starts Supervisor in the foreground.

The gateway uses Alpine-packaged `openvpn`, `dante-server`, and `supervisor`; it does not implement a proxy itself. Dante listens on `0.0.0.0:1080`, selects `tun0` as its external interface, supports SOCKS5 TCP `connect` requests, and executes ordinary target traffic as the dedicated `vpnproxy` user. Its proxy listener remains unavailable until the OpenVPN up hook starts or restarts it.

The generated IPv4 and IPv6 owner chains apply only to `vpnproxy` output. They allow `ESTABLISHED,RELATED` replies and `NEW` packets whose output interface is `tun0`, then drop every remaining packet. This prevents a proxy target connection from leaking through `eth0` or another non-tunnel route while still allowing the root-owned OpenVPN transport to establish the tunnel.

OpenVPN runs with `--persist-tun` and `--script-security 2`. Its initial up hook starts Dante. Before a reconnect changes tunnel state, the down hook sends `SIGSTOP` to Dante. The process and listening socket remain present, new SOCKS requests wait in the kernel, and the owner-only firewall remains the fail-closed egress boundary while tunnel traffic is unavailable. The next up hook sends `SIGCONT` followed by `SIGHUP`, allowing queued requests to continue after Dante reloads the current `tun0` interface configuration. `persist-tun` may retain interface state for some reconnect paths, but it is not a guarantee that `tun0`, its address, or routes survive a reconnect with changed pushed ifconfig. No browser process depends on that assumption.

## Browser Boundary

The browser launcher requires one strict `--vpn-proxy-server hostname:port` value. It resolves that hostname immediately before launch, retains the literal IP and port for the launched process, and uses TCP readiness against exactly that endpoint. The browser never checks gateway metadata, OpenVPN state, routes, or `tun0`.

The generated `@playwright/mcp` `0.0.77` configuration sets `launchOptions.proxy.server` to `socks5://<literal-ip>:<port>` and disables QUIC. Playwright derives the Chromium host-resolver rule from that proxy, rejects local target hostname resolution, and excludes the literal proxy IP so Chromium can still reach the SOCKS endpoint. The runtime must not add a second host-resolver rule because Chromium would let it replace Playwright's proxy exclusion. This keeps target DNS within SOCKS5/Dante and blocks Chromium's UDP/QUIC direct path. The browser egress policy provides a separate network-level guarantee that the browser pod can reach only the gateway TCP service and cluster DNS.

The Playwright image has one least-privilege startup boundary. When Docker starts it as root, the container entrypoint creates and assigns only `/output/.playwright-mcp`, `/runtime`, and `/runtime-profile`, then drops supplementary groups, group identity, and user identity before executing the requested command as `browser`. This permits a brand-new bind-mounted output root without running Chromium or MCP as root. When the platform already selects the browser UID, the same entrypoint creates accessible directories and executes directly without ownership or identity changes; this preserves the Kubernetes `runAsNonRoot` contract.

An OpenVPN reconnect may still interrupt a target TCP connection that was already in flight. The Playwright MCP process remains available. For its single workflow client, `browser_close` disposes the current MCP backend, browser context, and Chromium process; the next browser tool creates a fresh backend. Workflow runtime recovery uses that boundary only after a connection-level navigation failure, after a bounded wait, so Chromium does not retain failed-proxy state across the retry. This is browser-context recovery inside one action attempt, not an MCP proxy or a workflow-step retry.

The browser runtime still owns profile materialization, one persistent `userDataDir`, headed Chromium under `xvfb-run`, Playwright stealth setup, locale, timezone, viewport, and output artifacts. `BrowserLocaleConfig` is the shared source for context locale, HTTP language preference, profile language preferences, and `navigator.languages` override. Its defaults remain generic: `en-US` and `UTC`.

## Kubernetes Reference

The reference manifest contains two Deployment/Service pairs:

- `vpn-egress` mounts the full DataSource and `/dev/net/tun`, runs as root with only `NET_ADMIN`, and exposes TCP `1080`.
- `browser-mcp` runs the MCP service on TCP `8931`, mounts profile input through a `playwright_profile` subPath, has no tunnel device or `NET_ADMIN`, and receives `vpn-egress:1080` as its only proxy endpoint.

The reference requires the source PVC to contain `playwright_profile/`; an empty directory represents the first-run profile. This is a Kubernetes `subPath` prerequisite, not a browser runtime requirement for direct non-Kubernetes use.

`browser-mcp-egress-deny` permits only TCP `1080` to gateway pods and TCP/UDP `53` to kube-dns. The workflow executor remains in a separate pod or runtime and connects to the browser MCP Service. It does not join either network namespace and it does not receive OpenVPN credentials.

The profile writeback Job stays independent of the long-running browser Deployment. The browser Deployment must be scaled to zero before the Job copies and atomically publishes the mutable profile state.

## Verification Contract

Behavior tests cover strict OpenVPN input validation, generated gateway files and firewall policy, literal proxy configuration, launcher-owned SOCKS5 TCP reachability, and Kubernetes resource relationships. Platform TCP healthchecks own Playwright MCP service readiness; no separate always-successful readiness API or container default command exists. Container integration still requires a cluster or local container runtime with an actual `/dev/net/tun`, valid OpenVPN credentials, and a reachable VPN endpoint; unit tests cannot prove provider-side tunnel establishment or Internet egress.
