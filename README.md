# browser-runtime

Reusable run-local Playwright MCP runtime with persistent named profiles and optional platform-provided network proxies.

The repository owns browser process launch, the public MCP profile router, physical run-local profile directories, profile reset and writeback-candidate snapshots, stealth, locale, timezone, viewport, and one isolated backend. It does not own workflow orchestration, domain extraction, VPN config, tunnel protocols, SOCKS5 gateways, provider slots, or VPN lifecycle.

## Runtime Boundary

The platform supplies:

- an immutable `playwright_profile/` source directory, which may be empty;
- writable run-local profile, backend, output, and writeback-candidate roots;
- an immutable `proxy_by_name_map` from exact stable names to run-local SOCKS5 URLs.

Public MCP requests may contain structural query values:

- `profile=<physical-profile>`;
- `profile_source=<physical-source-profile>` only on a new MCP initialization;
- `network_proxy_name={zitadel_user_id}/{vpn_config.name}`.

The caller reads `network_proxy_name` from its exact browser input setting. The router validates only that supplied name against the map, never selects or distributes names, and removes only its own structural values before forwarding. Backend identity is the pair `(physical_profile, network_proxy_name)`. The same profile may therefore run concurrently through different proxies; different exact settings never force `browser_close` or create a profile lease conflict. An absent proxy name means direct browser egress.

Every proxied backend configures Chromium with the exact `socks5://` endpoint, disables QUIC, keeps target DNS at the proxy side, and does not configure direct-proxy fallback. The browser never reads VPN metadata, credentials, tunnel state, routes, or `tun0`. A dropped existing TCP connection remains an ordinary browser error for the caller to retry.

## Profiles

Named profiles use one independent backend process, config directory, output directory, and persistent working user-data directory per profile/proxy pair. Two pairs never open the same `userDataDir`. The isolated backend has no persistent profile. A named target without `profile_source` initializes its pair-local copy from the immutable source only when absent. An explicit source reset resolves the source under the same exact supplied proxy name, stops only the target pair, and atomically replaces its pair-local working copy under deterministic pair locks.

`POST /runtime/mcp-playwright-profile/writeback-candidate?profile=<physical-profile>&network_proxy_name=<stable-name>` stops the exact backend pair and atomically replaces the single candidate directory from its working copy. The latest successful candidate publication wins. Proxy identity does not become part of the profile bytes or writeback destination.

## Security

Browser and MCP processes run non-root. The image receives no VPN secret, S3 credential, Product DB credential, Kubernetes API token, or VPN control API. Network reachability is supplied by the platform and is not inferred from user input.

## Development

```bash
python -m pip install -e ".[browser,test]"
python -m pytest -q
python -m compileall browser_runtime
docker build --build-context workflow_container_contract=../workflow-container-contract -f docker/playwright/Dockerfile -t browser-runtime:local .
```
