# Repository Guidelines

## Scope
- This repository owns a reusable browser/VPN runtime capability only.
- Do not add marketplace, brand, size-chart, scraping-domain, or workflow-specific business logic.
- Keep the runtime boundary explicit: OpenVPN owns VPN connectivity, Playwright owns browser execution, and callers own domain extraction behavior.

## Python
- Every Python module, class, and function must have a docstring.
- Runtime configuration and runtime state objects must use strict Pydantic models.
- Tests must use `pytest`.

## Verification
- Run `python -m pytest -q` after Python behavior changes.
- Run `python -m compileall browser_vpn_runtime` before handoff.
- Re-read `README.md` and `doc/design/browser-vpn-runtime.md` after documentation changes that affect runtime boundaries.
