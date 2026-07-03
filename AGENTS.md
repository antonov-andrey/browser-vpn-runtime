# Repository Guidelines

## Scope
- This repository owns a reusable browser/VPN runtime capability only.
- Do not add domain-specific or workflow-specific business logic.
- Keep the runtime boundary explicit: OpenVPN owns VPN connectivity, Playwright owns browser execution, and callers own domain extraction behavior.
- The Playwright MCP runtime must expose one runtime-owned browser stack; consumers must not configure direct `@playwright/mcp`, direct `npx`, stage-local browser profiles, or caller-owned browser flags as replacements for this stack.
- Consumers must keep workflow executors outside the OpenVPN network namespace; only browser runtime processes may use the VPN network path.

## Python
- Every Python module, class, and function must have a docstring.
- Runtime configuration and runtime state objects must use strict Pydantic models.
- Tests must use `pytest`.

## Verification
- Run `python -m pytest -q` after Python behavior changes.
- Run `python -m compileall browser_vpn_runtime` before handoff.
- Re-read `README.md` and `doc/design/browser-vpn-runtime.md` after documentation changes that affect runtime boundaries.
