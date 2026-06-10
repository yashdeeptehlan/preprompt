# PrePrompt — Threat Model

This document is a working sketch of the threats PrePrompt cares about. It is
not exhaustive and is updated as the product evolves. See `SECURITY.md` for
how to report new findings.

## Assets

1. **User prompts and conversation history.** Often contain proprietary source
   code, internal architecture, and sometimes secrets.
2. **Anthropic API key.** The key the optimizer uses for Haiku calls — a stolen
   key is direct financial loss.
3. **Stripe billing state.** Subscription status drives access to paid
   features; forged events grant free service or trigger fraudulent emails.
4. **Supabase identity.** User profiles linking Stripe customer IDs to email
   addresses.
5. **Local prompt history (`~/.preprompt/history.db`, `activity.log`).** A
   long-lived record of everything the developer has typed.

## Trust boundaries

```
[Developer's IDE]
       │  stdin JSON (UserPromptSubmit hook)
       ▼
[ cli/hook.py — local process ]
       │  HTTPS (Anthropic API, key from ~/.preprompt/.env)
       ▼
[ Anthropic Claude Haiku ]

[Landing page browser]
       │  HTTPS, optional Bearer JWT
       ▼
[ backend/ — Railway container, behind reverse proxy ]
       │
       ├── HTTPS → Anthropic (optimizer)
       ├── HTTPS → Supabase REST (auth, demo usage, profiles)
       ├── HTTPS → Stripe (checkout, webhook verification)
       └── HTTPS → Resend (welcome email)
```

## Adversaries we model

| Actor | Capability | Mitigations |
| --- | --- | --- |
| Anonymous web attacker | Sends arbitrary HTTP to `preprompt.org/api/*` | Origin allow-list, signed Stripe webhooks (H-1), trusted-proxy XFF (H-4), SlowAPI rate limit, generic 5xx envelope (H-7) |
| Forged Stripe webhook | POSTs a `checkout.session.completed` event | `STRIPE_WEBHOOK_SECRET` is a startup precondition; missing secret → 503 |
| Prompt-injection author | Sneaks instructions into the user's prompt or chat history | Optimizer output is length-bounded, scanned for secrets, and stripped of role markers before being returned to the IDE (H-9) |
| Co-located UNIX user | Reads files in `$HOME` | `history.db`, `activity.log`, `.env`, `dashboard.token`, and the Cursor rule file are written `chmod 600` (L-8/9/13) |
| Compromised PyPI maintainer | Pushes a malicious `preprompt` release | `preprompt-update` requires explicit `--yes`; publish workflow runs in a `release` GitHub Environment with required reviewers; actions pinned by SHA (M-3, M-9) |
| Local user with shell access | Curls the dashboard | Dashboard binds 127.0.0.1, requires per-host token, rejects non-loopback peers (L-3) |
| Curious operator reading Sentry | Tries to pull prompts from error reports | `send_default_pii=False`, `include_local_variables=False`, `before_send` recursively scrubs `prompt`/`email`/etc. and strips stack-frame locals (H-6) |

## What this model deliberately does *not* try to defend against

- Anthropic ingesting prompts under their published privacy terms — that is a
  product disclosure, not a vulnerability.
- An attacker who has root on the developer's workstation. We hold ourselves
  responsible for the contents of `~/.preprompt`, not for the workstation.
- A user who chooses to install the package and run the CLI; we assume the
  CLI's code path is trusted by the user.

## Open follow-ups

- Sigstore signing of PyPI releases (currently relies on GitHub OIDC trusted
  publishing alone).
- Per-event encryption-at-rest for `history.db` (currently relies on filesystem
  permissions).
- Telemetry opt-in switch in `preprompt-install` (currently opt-out via env
  vars).
