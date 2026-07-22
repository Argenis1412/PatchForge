# Security Policy

## Supported Versions

PatchForge is in active alpha development (`Development Status :: 3 - Alpha`). Security fixes are provided for the latest tagged release on GitHub only.

## Reporting a Vulnerability

If you believe you've found a security vulnerability in PatchForge, please report it privately by email to **argenisbackend@gmail.com** rather than opening a public issue.

Include, if possible:

- A description of the vulnerability and its potential impact
- Steps to reproduce (minimal repro case, if available)
- The affected version or commit

PatchForge is maintained by a single author. There is no formal SLA, but reports will be acknowledged and investigated as promptly as possible. Please allow reasonable time for a fix before any public disclosure.

## Scope

PatchForge already applies security hardening in several areas — path traversal protection, safe module resolution, and fail-closed audit export verification (see [CONTEXT.md](./docs/context/CONTEXT.md) and the [ADRs](./docs/adr/) for details). This policy covers reporting *new* findings, not a list of currently open issues.
