# Repository Guidelines

## Scope
- This repository owns the reusable browser runtime capability only.
- Shared workflow-container ecosystem authoring and code quality rules live in the `workflow-container-tools` plugin reference `references/workflow-container-authoring.md`.
- Do not add domain-specific or workflow-specific business logic.
- Keep the runtime boundary explicit: this repository owns Playwright execution and profiles, `vpn-runtime` owns VPN connectivity and SOCKS5 gateways, and callers own domain extraction behavior.
- The Playwright MCP runtime must expose one runtime-owned browser stack; consumers may select logical run-local profile names through the workflow contract but must not configure direct `@playwright/mcp`, direct `npx`, physical profile paths, profile-copy operations, or caller-owned browser flags as replacements for this stack.
- The router must treat physical profile plus the exact optional stable network proxy name supplied by the caller as the backend identity and must validate that name against the immutable platform-provided proxy map; it must not select or distribute proxy names.
- This repository must not own VPN config parsing, OpenVPN, WireGuard, `tun0`, SOCKS5 server lifecycle, provider connection slots, or VPN validation.

## Python
- Python code uses Python 3.14.
- Python code must be formatted with Black using target version `py314` and line length `120`.
- Public API, stable runtime boundaries, and non-trivial modules must have docstrings that describe real behavior.
- Runtime configuration and runtime state objects must use strict Pydantic models.
- Tests must use `pytest`.
- Tests must not verify instruction artifacts by checking that specific prose, headings, phrases, examples, files, or placement rules exist or do not exist. Instruction artifacts are verified by semantic reread or semantic audit, not by pytest assertions over text or instruction artifact paths.

## Verification
- Run `python -m pytest -q` after Python behavior changes.
- Run `python -m compileall browser_runtime` before handoff.
- Re-read `README.md` and `doc/design/browser-runtime.md` after documentation changes that affect runtime boundaries.
