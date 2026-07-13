# Browser VPN Runtime

`browser-vpn-runtime` is a reusable browser capability with a stable SOCKS5 boundary between browser execution and OpenVPN. It owns browser profile preparation, runtime-owned Playwright MCP startup, and a separately deployed VPN egress gateway. It does not own workflow orchestration, extraction behavior, or domain-specific logic.

## Runtime Layout

A DataSource contains optional browser profile state and the gateway-owned OpenVPN input:

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

`openvpn/config.json` has the strict form:

```json
{"login": "vpn-user", "openvpn_config_name": "client.ovpn", "password": "vpn-password"}
```

The config name is one local `.ovpn` file name. It cannot contain `/`, `..`, or an absolute path, and it must exist below `openvpn/`. The gateway validates this input and writes a mode-`0600` `--auth-user-pass` file in its writable runtime directory. Browser pods mount only `playwright_profile/`; they do not receive the OpenVPN secret tree.

## VPN Egress Gateway

`browser-vpn-runtime-vpn-egress` runs OpenVPN and Alpine's packaged Dante `sockd` under Alpine's packaged `supervisord`. It writes the Dante, supervisor, firewall, and OpenVPN hook files into `/runtime/vpn-egress/`, installs its firewall before the supervisor starts, and never rewrites the DataSource `.ovpn` file. Before installing tunnel-only target DNS, the gateway resolves every named OpenVPN `remote` through the container's bootstrap resolver and pins the resulting addresses in the container hosts file. OpenVPN reconnects therefore do not depend on DNS routed through the tunnel. A container restart resolves the provider endpoints again. Dante's unprivileged target DNS traffic can reach the configured public resolvers only through `tun0`.

Dante listens on TCP `1080` and runs target traffic as the unprivileged `vpnproxy` user. Its generated IPv4 and IPv6 output chains allow only established replies and new `vpnproxy` traffic with output interface `tun0`; every other `vpnproxy` output packet is dropped. The gateway can still establish OpenVPN as root, but Dante cannot fall back to `eth0` when the tunnel disappears.

OpenVPN `up` and `down` hooks control Dante through Supervisor. Initial tunnel readiness starts Dante. Before reconnect changes tunnel state, the down hook sends `SIGSTOP`: Dante retains its stable listening socket and the kernel queues new SOCKS requests instead of returning a false terminal proxy error. Its owner-only firewall still blocks any fallback outside `tun0`. The next up event sends `SIGCONT` and `SIGHUP`, so queued requests resume against the current tunnel configuration without replacing the listener process. `--persist-tun` remains enabled as a reconnect optimization, but it does not guarantee preservation of `tun0`, its address, or its routes when the server pushes changed interface configuration. Browser processes are insulated from that lifecycle because they never share the gateway network namespace.

Run the gateway directly:

```bash
browser-vpn-runtime-vpn-egress \
  --data-source-path /input/.secret \
  --runtime-path /runtime
```

## Playwright MCP

`browser-vpn-runtime-playwright-mcp` requires exactly one `--vpn-proxy-server hostname:port` endpoint. Before Chromium starts, the launcher resolves the endpoint once to a literal IP address, waits for that IP and port to accept TCP, then writes the `@playwright/mcp` `0.0.77` configuration with:

- a `socks5://<literal-ip>:<port>` Playwright launch proxy;
- `--disable-quic`;
- Playwright-owned Chromium host-resolver rules that keep target DNS out of the browser pod while excluding the literal proxy IP;
- no direct-proxy fallback configuration.

The launcher must not add another `--host-resolver-rules` argument. Chromium accepts only one effective rule, and a later generic rule would replace Playwright's proxy-IP exclusion and make the configured SOCKS endpoint unreachable.

SOCKS5 target hostname resolution stays at the gateway side. The Playwright MCP launcher materializes the browser profile, resolves the configured gateway once, and waits for that exact SOCKS5 TCP endpoint. Platform TCP healthchecks own service readiness; there is no separate runtime readiness command.

The Playwright image entrypoint prepares only its fixed writable roots, including `/output/.playwright-mcp`, before browser startup. Under ordinary Docker bind mounts it starts with root privileges solely to create and assign those roots, then replaces itself with the requested command as the unprivileged `browser` user. When an orchestrator already starts the image as that browser UID, as in the Kubernetes reference, it creates accessible roots without changing ownership or identity. Browser and MCP processes never run as root.

An already active target connection can still fail while OpenVPN reconnects. In that case the workflow runtime keeps the error in its browser result, waits for gateway recovery, calls the standard Playwright MCP `browser_close` tool, and reopens the same target. `browser_close` disposes the current browser backend while the MCP server stays available; the next browser tool starts a fresh Chromium network context without restarting the workflow step. The configured MCP server exposes one shared browser context, so callers must not operate that endpoint concurrently; the workflow runtime serializes concurrent invocations by MCP URL.

```bash
browser-vpn-runtime-playwright-mcp \
  --data-source-path /input/.secret \
  --persistent-profile-path /runtime-profile/playwright_profile \
  --output-dir /output/.playwright-mcp/current \
  --mcp-config-path /runtime/playwright_mcp/config.json \
  --host 0.0.0.0 \
  --allowed-hosts localhost,127.0.0.1,browser-mcp \
  --port 8931 \
  --vpn-proxy-server vpn-egress:1080
```

`--output-dir` must be inside the runtime-owned `.playwright-mcp` artifact namespace. The container entrypoint makes that namespace writable even when the caller supplies a new bind-mounted output root. Generated MCP configuration, the stealth script, and mutable browser profile remain outside that artifact root. `BrowserLocaleConfig` is the single typed owner of browser locale, HTTP language header, Chromium preferences, and stealth navigator language values. Its neutral defaults are `en-US` and `UTC`.

## Kubernetes Reference

`deploy/k8s/runtime-capability.yaml` declares independent `vpn-egress` and `browser-mcp` Deployments and matching Services. Only `vpn-egress` gets `NET_ADMIN`, `/dev/net/tun`, and the full read-only DataSource. `browser-mcp` has no tunnel device or network-administration capability and receives only the profile subdirectory. Its egress NetworkPolicy permits TCP `1080` to the gateway and TCP/UDP `53` to kube-dns; direct target traffic is denied.

The source PVC used by this reference must contain `playwright_profile/`; an empty directory is valid for a first-run profile. Kubernetes requires the source of this read-only `subPath` mount to exist before the browser pod starts.

Workflow execution remains external to both pods. It connects to `http://browser-mcp:8931/mcp` and must not launch `@playwright/mcp`, `npx`, or an independent browser stack.

## Development

```bash
python -m pip install -e ".[browser,test]"
.venv/bin/pytest -q
python -m compileall browser_vpn_runtime
```
