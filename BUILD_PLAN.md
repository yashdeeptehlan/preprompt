# PrePrompt — Build Plan (Shared Checklist)

**Owners:** Yashdeep (Y), Vishal (V)
**Last updated:** 2026-06-13
**Estimates:** hours of focused work with Claude Opus 4.7 pair-coding. Add ~30% for testing, review, and PR cycle.

Mark items `[x]` as done. Whoever finishes an item updates this file and commits with `chore: tick BUILD_PLAN — <item>`.

---

## Status legend

- `[ ]` Not started
- `[~]` In progress (add owner initial in trailing tag)
- `[x]` Done
- `[!]` Blocked (add reason in trailing tag)

---

## P0 — Trust + retention layer (ship before any public launch)

These three are gated by Yashdeep's forensic audit. No HN, no PH, no Twitter push until all three are live and have telemetry running for at least 7 days.

| Done | Item | Est | Owner | Files | EXECUTION ref |
|------|------|-----|-------|-------|---------------|
| [x] | **W2 — One-click revert + `preprompt-revert` CLI** | 2h | V | `cli/hook.py`, `cli/commands.py`, `pyproject.toml` | line 889–933 |
| [x] | **W4 — First-prompt onboarding message** | 1h | V | `cli/hook.py` | line 994–1028 |
| [x] | **Accept/reject UX in IDE annotation + `preprompt-rate keep\|revert` command** | 4h | V | `cli/hook.py`, `cli/commands.py`, `storage/db.py` (already has `record_user_feedback`) | forensic audit §21 |
| [x] | **Wire all PostHog events end-to-end** (`prompt_processed`, `enrichment_accepted`, `enrichment_rejected`, `install_completed`, `secret_detected`) | 3h | V | `cli/hook.py`, `mcp_server/tools.py`, `backend/main.py` | EXECUTION line 421–523 |

**Subtotal P0: 10 hours.** One day of focused work, two days realistic.

---

## P1 — Measurement + dashboard polish

You can't gate on retention if no one is reading the dashboard. These items make the data usable.

| Done | Item | Est | Owner | Files |
|------|------|-----|-------|-------|
| [ ] | **NW1 — Before/after diff view in dashboard** (expandable rows) | 3h | V | `dashboard/static/index.html` only |
| [ ] | **Accept rate prominently on dashboard** (big number + trend) | 1h | V | `dashboard/static/index.html`, `storage/db.py` (query exists) |
| [ ] | **`preprompt-stats` redesign** (route ratios, accept rate, top intercepted patterns) | 2h | V | `cli/commands.py` |
| [ ] | **Weekly metrics email script** (Mon morning summary to both founders) | 2h | V | new `scripts/weekly_metrics.py` |
| [ ] | **D30 retention SQL query in `storage/db.py`** | 2h | V | `storage/db.py` |

**Subtotal P1: 10 hours.**

---

## P2 — Paid tier completion (what makes it a business)

Most paid-tier infra already shipped (Stripe, auth modal, demo widget). These are the missing pieces that prevent revenue leak.

| Done | Item | Est | Owner | Files |
|------|------|-----|-------|-------|
| [ ] | **W1 — Usage metering for paid plans** (Supabase `usage_events` table + `_record_usage_event()`) | 5h | Y | `backend/main.py` + Supabase SQL |
| [ ] | **Free tier hard cap at 30 enrichments/month** (currently 2 demo tries) | 1h | Y | `backend/main.py` |
| [ ] | **Pricing migration to Free / $14 Dev / $29 Pro / Team** (Stripe + landing page) | 3h | V (landing) + Y (Stripe) | `docs/index.html`, Stripe dashboard |
| [ ] | **`preprompt login` CLI command** (browser → magic link → `~/.preprompt/.env`) | 4h | Y | new `cli/auth.py`, `cli/commands.py` |
| [ ] | **Hosted optimizer endpoint** (`POST /v1/optimize` w/ PrePrompt key — server holds Anthropic key) | 6h | Y | `backend/main.py` |
| [ ] | **Pro mode toggle in hook** (if PrePrompt key present, call our endpoint instead of Anthropic) | 2h | Y or V | `cli/hook.py`, `mcp_server/optimizer.py` |

**Subtotal P2: 21 hours.**

---

## P3 — Personalization (retention compounding)

The forensic audit calls these the switching-cost moats. Build after P0 ships and accept-rate data confirms the core works.

| Done | Item | Est | Owner | Files |
|------|------|-----|-------|-------|
| [ ] | **NW2 — Project profiles** (git-repo-keyed memory, schema migration) | 8h | Y (schema), V (CLI detection) | `storage/db.py`, `mcp_server/extractor.py`, `cli/hook.py`, `mcp_server/optimizer.py` |
| [ ] | **Project-aware optimizer context injection** | 2h | Y | `mcp_server/optimizer.py` |
| [ ] | **Dashboard: select project filter** | 2h | V | `dashboard/static/index.html` |

**Subtotal P3: 12 hours.**

---

## P4 — Documentation + content scaffold

Pre-launch table stakes. Without these, HN comments destroy you in the first hour.

| Done | Item | Est | Owner | Files |
|------|------|-----|-------|-------|
| [ ] | **Mintlify docs site at `docs.preprompt.org`** (scaffold + DNS) | 3h | V | new `/docs-site/` |
| [ ] | **Getting started** (5-min install → first optimization) | 2h | V | `/docs-site/getting-started.mdx` |
| [ ] | **How the classifier works** (with examples) | 2h | V | `/docs-site/classifier.mdx` |
| [ ] | **Privacy page** (what leaves your machine, what doesn't) | 1h | V | `/docs-site/privacy.mdx`, `docs/index.html` link |
| [ ] | **FAQ — top 10 questions** | 1h | V | `/docs-site/faq.mdx` |
| [ ] | **Privacy policy + Terms of Service** (lawyer-free templates, link from footer) | 2h | V | `docs/privacy.html`, `docs/terms.html` |

**Subtotal P4: 11 hours.**

---

## P5 — VS Code extension (the biggest distribution unlock)

New repo: `preprompt-ai/vscode-extension`. TypeScript. Calls existing backend. Yashdeep's audit ranks this as the #1 GTM move.

Don't start until P0 is done and accept-rate ≥55% on 20+ users.

| Done | Item | Est | Owner | Notes |
|------|------|-----|-------|-------|
| [ ] | **Scaffold + Marketplace publisher account** | 3h | V | TypeScript template, `yo code` |
| [ ] | **Intercept chat input via VS Code Copilot Chat API** | 4h | V | API surface check first |
| [ ] | **Call backend `/v1/optimize`, render rewrite in side panel** | 4h | V | depends on P2 hosted endpoint |
| [ ] | **Accept / revert UI + keyboard shortcut** (`Cmd+Shift+P`) | 3h | V | |
| [ ] | **Supabase OAuth flow inside extension** | 5h | V | browser-based redirect |
| [ ] | **Telemetry events to PostHog** | 2h | V | mirror cli/hook.py events |
| [ ] | **Marketplace submission + iterate on review** | 3h | V | review takes 3-5 business days wall-clock |

**Subtotal P5: 24 hours.**

---

## P6 — Browser extension MVP (consumer wedge)

New repo: `preprompt-ai/browser-extension`. Manifest V3, TypeScript, three sites only: chatgpt.com, claude.ai, gemini.google.com.

Don't start until P5 ships to Marketplace.

| Done | Item | Est | Owner | Notes |
|------|------|-----|-------|-------|
| [ ] | **Scaffold + Chrome Web Store dev account** | 3h | V | $5 one-time fee |
| [ ] | **Content script: intercept textarea + send button on ChatGPT** | 6h | V | DOM fragile, brace for breakage |
| [ ] | **Same for Claude.ai + Gemini** | 6h | V | each one is different |
| [ ] | **Popup UI: original vs rewrite, send original or send rewrite** | 4h | V | React inside popup |
| [ ] | **Auth flow (same Supabase, browser-based)** | 3h | V | |
| [ ] | **Telemetry** | 2h | V | |
| [ ] | **Submission + Store review** | 3h | V | 1-3 days wall-clock |

**Subtotal P6: 27 hours.**

---

## P7 — Infrastructure for 1K+ users

Don't build before P0-P5 ship — premature optimization. Build before launching publicly.

| Done | Item | Est | Owner | Files |
|------|------|-----|-------|-------|
| [ ] | **Upstash Redis** (rate limiting + atomic counters — fixes T4 race condition) | 4h | Y | `backend/main.py` |
| [ ] | **Local JWT verification** (eliminate per-request Supabase HTTP call) | 3h | Y | `backend/main.py` |
| [ ] | **Structured logging with request IDs** | 3h | Y | `backend/main.py` + log middleware |
| [ ] | **Uptime monitoring** (Better Uptime free tier) | 1h | V | external service, no code |
| [ ] | **Sentry alert rules** (error rate >1%, p95 >2s, Anthropic failures) | 1h | V | Sentry dashboard |

**Subtotal P7: 12 hours.**

---

## P8 — Cloud dashboard (Next.js, replaces Babel JSX)

After paid tier is real and a customer has asked "where's my dashboard."

| Done | Item | Est | Owner | Files |
|------|------|-----|-------|-------|
| [ ] | **Next.js scaffold on Vercel** | 3h | V | new `web/` |
| [ ] | **Auth pages (signin/signup) using Supabase JS** | 4h | V | |
| [ ] | **Dashboard home: stats from Supabase** | 6h | V | |
| [ ] | **History + diff page** | 4h | V | |
| [ ] | **Pricing page (replaces section in `docs/index.html`)** | 3h | V | |
| [ ] | **DNS: `dashboard.preprompt.org`** | 1h | V | |

**Subtotal P8: 21 hours.**

---

## Totals

| Phase | Hours | Cumulative | Notes |
|-------|-------|------------|-------|
| P0 trust + retention | 10 | 10 | Pre-launch blocker |
| P1 measurement + dashboard | 10 | 20 | Pre-launch blocker |
| P2 paid tier completion | 21 | 41 | Before paid launch |
| P3 personalization | 12 | 53 | Defends retention |
| P4 docs + content | 11 | 64 | Pre-launch blocker |
| P5 VS Code extension | 24 | 88 | Distribution unlock #1 |
| P6 browser extension | 27 | 115 | Consumer wedge |
| P7 infrastructure | 12 | 127 | Before public scale |
| P8 cloud dashboard | 21 | 148 | Before $5K MRR |

**148 hours of focused work.** Two people working 6 focused hours/day = ~12 working days minimum. Realistic with reviews, meetings, fires: **4–6 weeks**.

---

## Daily rules

1. **One PR per item.** Branch named `feat/<item-slug>` or `fix/<item-slug>`. Open as draft on day 1, mark ready when tests pass.
2. **Don't both touch the same file in the same day.** Coordinate in 1:1.
3. **Mark `[~]` the moment you start.** Mark `[x]` only when merged to main.
4. **Untracked off-limits:** `mcp_server/optimizer.py` system prompt + `mcp_server/classifier.py` thresholds. Only Yashdeep edits these. Vishal: open an issue if you want to propose a change.
5. **Weekly review every Monday 9am.** Update this file together. Anything blocked >3 days gets escalated or descoped.

---

## Currently in flight

- Security audit branch → main. **Pushed 2026-06-13.** ✅

---

## Decisions outstanding (block items above)

These came from Yashdeep's `Complete Audit & Master Roadmap`. Resolve in next 1:1:

- [ ] Pricing migration: do we move to $14/$29/Team now, or after first 50 paid users? *Affects: P2 items.*
- [ ] Core package extraction: now (clean, 1 week) or after first hire? *Affects: P5/P6 architecture.*
- [ ] Launch timing: early (day 30) or retention-gated (day 60+)? *Affects: all P-priority sequencing.*
- [ ] Upstash Redis account: who creates it, when? *Blocks P7.*
- [ ] PostHog project key for `docs/index.html`: paste it in. *Blocks P0 wire-up.*
- [ ] Runway: months of cash on hand? *Affects: hire timing.*

---

## Notes on estimates

Estimates assume Opus 4.7 pair-coding with one human reviewer. Patterns:

- "1h" items: single file, ≤50 LOC, clear spec in `EXECUTION.md` or audit docs.
- "2-3h" items: 2-3 files, moderate logic, some tests to write.
- "4-6h" items: schema migration or new auth surface, requires integration test.
- "8h+" items: new product surface or cross-cutting change touching ≥5 files.

If something is taking 2× the estimate, stop and re-plan rather than push through. Almost certainly a missing dependency or unspoken constraint.
