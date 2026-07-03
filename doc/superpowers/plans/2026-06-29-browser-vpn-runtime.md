# Реализация `browser-vpn-runtime`

**Цель:** создать отдельный reusable runtime repo для browser/VPN capability, который может подключаться к workflow container-ам и не содержит domain logic конкретного workflow.

## Файловая Структура

- Create: `AGENTS.md` — минимальные правила проекта и границы runtime capability.
- Create: `README.md` — назначение, secret layout, dev run, Kubernetes contract.
- Create: `pyproject.toml`, `requirements.txt`.
- Create: `browser_vpn_runtime/config.py` — validated config для secret paths, OpenVPN и Playwright profile.
- Create: `browser_vpn_runtime/openvpn.py` — проверка `openvpn/config.json` и имени `.ovpn`.
- Create: `browser_vpn_runtime/playwright_profile.py` — materialization/snapshot helpers для `playwright_profile/**`.
- Create: `browser_vpn_runtime/runtime.py` — browser runtime boundary и readiness checks.
- Create: `browser_vpn_runtime/__init__.py`.
- Create: `docker/openvpn/Dockerfile`, `docker/playwright/Dockerfile`.
- Create: `deploy/k8s/runtime-capability.yaml` — reference pod/container wiring для общего network namespace.
- Test: `test/test_openvpn_config.py`, `test/test_playwright_profile.py`, `test/test_runtime_contract.py`.
- Create: `doc/design/browser-vpn-runtime.md`.

## Task 1: Runtime Contracts

- [ ] Описать conventional private `DataSource` paths: `openvpn/config.json`, `openvpn/<name>.ovpn`, `playwright_profile/**`, `codex_profile/**`.
- [ ] Реализовать strict validation `openvpn_config_name`: без `/`, `..`, absolute path.
- [ ] Реализовать profile materialization в pod-local directory без чтения domain-specific files.

## Task 2: Browser/VPN Boundary

- [ ] Добавить runtime config для timezone, locale, viewport и persistent profile path.
- [ ] Зафиксировать, что весь browser-bound extraction идет через Playwright page/context, а direct HTTP не является основной веткой.
- [ ] Добавить readiness API/CLI, который проверяет наличие profile dir, OpenVPN config и browser launch prerequisites.

## Task 3: Container/Kubernetes Assets

- [ ] Добавить OpenVPN sidecar Dockerfile.
- [ ] Добавить Playwright runtime Dockerfile или package-level entrypoint для app containers.
- [ ] Добавить reference Kubernetes manifest с общим pod network namespace и secret volume mount contract.

## Task 4: Verification

- [ ] Запустить `python -m pytest -q`.
- [ ] Запустить `python -m compileall browser_vpn_runtime`.
- [ ] Проверить `README.md` и `doc/design/browser-vpn-runtime.md` на соответствие cross-project spec.
