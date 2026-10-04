# Security Policy

palimem holds beliefs that agents act on, so we treat memory integrity, admission and privacy as security matters. The threat model is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Reporting a vulnerability

Please report privately. **Do not open a public issue for a vulnerability.**

Use GitHub private vulnerability reporting for this repository:

1. Go to <https://github.com/chleiva/palimem/security/advisories/new> (the **Security** tab → **Report a vulnerability**).
2. Describe the issue, the affected version or commit, and how to reproduce it. A minimal conformance-style fixture (a short JSON sequence of reports and the unexpected answer) is ideal.
3. If you cannot use GitHub, open a public issue that says only that you have a security report and asks for a private channel. Do not include details.

What to expect: this is a pre-1.0, single-maintainer project. We aim to acknowledge reports within 7 days and to agree a disclosure timeline with you; we will credit reporters who want credit. We cannot offer a bounty.

## Supported versions

| Version | Supported |
|---|---|
| `main` (development) | Yes, fixes land here first |
| Latest `0.x` release | Yes, best effort |
| Older `0.x` releases | No. Contracts may break at any `0.x` release; upgrade |
| `1.x` (once released) | Latest minor receives security fixes; older minors by announcement |

Before 1.0 there is no long-term support. A fix may require a contract change and a new `0.x` release. The `0.0.x` releases are name reservations with no functionality.

## Scope

In scope:

- The palimem library and the agent tool API (admission, authority, quarantine, kernel, store, policy, extractor interface, outbox, deletion).
- The MCP server and any network listener we ship.
- The storage layer: log and chain integrity, erasure, injection.
- Our packaging and release pipeline, and the names we publish (`palimem` on PyPI and npm).
- The conformance suite, when a fixture encodes insecure expected behaviour.

Examples we want to hear about: a way to make an LLM-supplied argument set `source`, `origin`, `actor` or `origin_group`; a report that gets admitted without confirmation; forging a withdrawal or merge without authority; reading beliefs outside a principal's scope; erasure that leaves recoverable content; an HTTP transport reachable from a browser origin; a crash or hang from a crafted report.

Out of scope:

- Code execution inside the host application or the palimem process.
- Attacks that need write access to the process's own memory or environment.
- A trusted source that lies. The system reports what the evidence justifies, not what is true.
- Behaviour of third-party services (LLM providers, MCP clients, other MCP servers), except where palimem's use of them is itself unsafe.
- The unrelated PyPI package named `palimpsest`, which is not this project. The correct install name is `palimem`.
- Findings that apply only to the study's benchmark data or scripts (report those to the `chleiva/palimpsest` repository).

## Our practices

- Credentials never live in the repository. A secret scan runs as a pre-commit hook and in CI.
- Releases are published with PyPI trusted publishing and two-factor authentication on all publishing accounts.
- The core has no third-party dependencies; optional extras are pinned and kept minimal.
- Security-relevant behaviour is specified as conformance fixtures, so regressions fail CI.

This policy will be revised as the threat model matures and at the 1.0 release.
