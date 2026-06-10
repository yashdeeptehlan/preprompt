# Security Policy

## Supported versions

PrePrompt is alpha software. Security fixes are applied to the latest published
release on PyPI. Older releases do **not** receive backports.

| Version range | Supported |
| --- | --- |
| Latest minor (currently `0.1.x`) | ✅ |
| Anything older | ❌ |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports. Send the
report to `security@preprompt.org` (or, if that bounces, to the maintainer
listed in `pyproject.toml`'s author field). PGP-encrypted reports are welcome
— request the public key in your first message and we'll reply with the
fingerprint over the same channel.

Include in your report:
- A description of the issue and the impact you believe it has.
- A proof-of-concept or step-by-step reproduction.
- Whether the issue is publicly known or actively exploited.

### Response SLA

- Acknowledgement within **3 business days**.
- Triage and severity assessment within **7 business days**.
- Fix or mitigation plan within **30 days** for high-severity issues; longer
  for low-severity ones. We will keep you updated weekly while a fix is in
  flight.
- We coordinate disclosure with the reporter — please give us a reasonable
  embargo period (90 days unless otherwise agreed) before publishing details.

## Scope

In scope:
- The `preprompt` Python package and its CLI entry points.
- The `mcp_server` and `cli/hook.py` runtime that intercepts prompts.
- The demo backend at `https://preprompt.org/api/*` (Railway deployment).
- The dashboard (`dashboard/server.py`) when bound to loopback.

Out of scope:
- Third-party services we integrate with (Anthropic, Stripe, Supabase, Resend,
  PostHog, Sentry, Railway) — report those upstream.
- Social-engineering attacks against PrePrompt staff or contributors.
- Findings that require local root or physical access to a user's machine.

## What we will *not* do

- We will not threaten legal action against good-faith researchers reporting
  in line with this policy.
- We will not publicly identify reporters without consent.

## Bug bounty

PrePrompt does not currently run a paid bounty program. We will publicly
credit researchers (with permission) in the release notes for the fix.
