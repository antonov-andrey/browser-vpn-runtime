# Repository Guidelines

## Scope
- This repository owns a reusable browser/VPN runtime capability only.
- Shared workflow-container ecosystem authoring and code quality rules live in the `workflow-container-developer` plugin reference `references/workflow-container-authoring.md`.
- Do not add domain-specific or workflow-specific business logic.
- Keep the runtime boundary explicit: OpenVPN owns VPN connectivity, Playwright owns browser execution, and callers own domain extraction behavior.
- The Playwright MCP runtime must expose one runtime-owned browser stack; consumers may select logical run-local profile names through the workflow contract but must not configure direct `@playwright/mcp`, direct `npx`, physical profile paths, profile-copy operations, or caller-owned browser flags as replacements for this stack.
- Consumers must keep workflow executors and Playwright outside the OpenVPN network namespace; only the `vpn-egress` gateway owns OpenVPN and `tun0`, while Playwright reaches that gateway through SOCKS.

## Python
- Python code uses Python 3.14.
- Python code must be formatted with Black using target version `py314` and line length `120`.
- Public API, stable runtime boundaries, and non-trivial modules must have docstrings that describe real behavior.
- Runtime configuration and runtime state objects must use strict Pydantic models.
- Tests must use `pytest`.
- Tests must not verify instruction artifacts by checking that specific prose, headings, phrases, examples, files, or placement rules exist or do not exist. Instruction artifacts are verified by semantic reread or semantic audit, not by pytest assertions over text or instruction artifact paths.

## Verification
- Run `python -m pytest -q` after Python behavior changes.
- Run `python -m compileall browser_vpn_runtime` before handoff.
- Re-read `README.md` and `doc/design/browser-vpn-runtime.md` after documentation changes that affect runtime boundaries.
