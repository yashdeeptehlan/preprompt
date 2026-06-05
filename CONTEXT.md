<!-- Last updated: 2026-05-11 -->
# PrePrompt — CONTEXT.md
# This file is auto-maintained. Read it fully at the start of every chat.

## Build status
Phase 10 complete. 29/29 tests passing.

## What PrePrompt does
MCP server that intercepts prompts in Claude Code and Cursor, scores them
with a heuristic classifier (no API), optimizes complex ones using Claude
Haiku, logs everything to SQLite (WAL mode), and learns the user's stack
over time via a memory layer. Runs entirely locally.

## Tech stack
- Python 3.11+, FastMCP, Anthropic SDK, SQLite (WAL mode), pydantic-settings
- Haiku model: claude-haiku-4-5-20251001
- DB: SQLite WAL at ~/.preprompt/history.db

## File map
mcp_server/
  server.py      — entry point, mcp.run(transport=settings.mcp_transport)
  tools.py       — MCP tools: optimize_prompt, get_prompt_history
                   uses get_or_create_session() for stable daily session identity
  classifier.py  — pure heuristic scorer, threshold=38, multi-req weight=12/hit, no API calls
  optimizer.py   — Haiku API call + memory context injection
                   strips markdown fences from model JSON response
  extractor.py   — heuristic stack signal extractor
  config.py      — pydantic-settings, reads .env

storage/
  db.py          — SQLite WAL: prompt_history + stack_memory + sessions tables
                   prompt_history has user_kept column (NULL=unrated, 1=kept, 0=rejected)
                   record_user_feedback(event_id, kept) — saves accept/reject
                   get_feedback_stats() — returns accept_rate, kept, rejected counts
                   get_route_stats() — returns pass/enrich/clarify counts + total
                   get_stack_memory_with_confidence() — returns full memory list for dashboard
                   get_all_history(): cross-session history query, now includes route col, default limit=100
                   Sidecar pattern: hook writes JSON to ~/.preprompt/pending/,
                   flushed by MCP server or CLI commands via flush_pending_hook_events()
                   flush_pending_hook_events() also calls update_memory_from_prompt()
                   so Claude Code sessions contribute to stack memory
                   get_or_create_session(): stable {hostname}-{date} session key
                   race-safe: INSERT OR IGNORE + _session_lock (threading.Lock)
                   handles Cursor spawning multiple MCP server processes simultaneously
                   upsert_stack_memory(): compounding confidence (+0.03/hit, reset on value change)

dashboard/
  __init__.py    — empty package marker
  server.py      — FastAPI app at port 7777: /api/stats, /api/history, /api/routes,
                   /api/memory, / (HTML); CORS; uvicorn main()
  static/
    index.html   — single-file SPA: Chart.js doughnut charts, paper/amber design,
                   stats strip, route breakdown, accept/reject chart, history table,
                   stack memory with confidence bars; auto-refresh every 30s

cli/
  commands.py    — preprompt-history, stats, memory, test-classifier,
                   clip (cross-platform clipboard optimizer), optimize,
                   feedback (accept/reject rating), install (one-command setup),
                   update, update-context, dashboard (launches FastAPI at :7777)
                   stats_cmd/history_cmd call maybe_run_setup() on first run
                   install_cmd/update_cmd use cli._register.register_hooks() — no file-path subprocess
  setup.py       — first-run API key wizard (maybe_run_setup()); after key saved, calls register_hooks()
  hook.py        — pip-installable hook (run as: python -m cli.hook)
                   loads API key from ~/.preprompt/.env, no sys.path manipulation
                   identical logic to .claude/hooks/pre_prompt.py
  _register.py   — register_hooks(api_key): writes MCP + UserPromptSubmit to ~/.claude/settings.json
                   uses sys.executable -m mcp_server.server and sys.executable -m cli.hook
                   called by install_cmd, update_cmd, maybe_run_setup
  watch.py       — preprompt-watch: auto-flushes sidecars on startup, live tail of ~/.preprompt/activity.log

.claude/
  settings.json           — MCP server + UserPromptSubmit hook config
  hooks/pre_prompt.py     — interception hook with rich box annotation on stderr
                            path resolved via __file__ (CWD-independent)
                            writes JSON sidecar to ~/.preprompt/pending/ — never touches DB directly
                            appends every event to ~/.preprompt/activity.log via _log_activity()

scripts/
  install.sh              — one-command installer (auto-detects Claude Code, Cursor, Windsurf, Zed)
  setup_global_hook.py    — global Claude Code MCP + UserPromptSubmit registration
  install_cursor.py       — registers MCP in ~/.cursor/mcp.json
  install_windsurf.py     — registers MCP in ~/.codeium/windsurf/mcp_config.json
  install_zed.py          — registers MCP in ~/.config/zed/settings.json
  init_github.py          — git init + first commit + push instructions

.github/
  workflows/ci.yml        — runs pytest on every push/PR
  workflows/publish.yml   — builds + publishes to PyPI on git tag v*

preprompt.skill.md        — Claude Skill file for tools without MCP hook support

LICENSE                   — MIT

tests/
  test_classifier.py    — 12 tests
  test_optimizer.py     —  4 tests
  test_integration.py   — 13 tests (incl. activity log write test)

## Key interfaces — never change these signatures
- classify_prompt(prompt: str, history: list, turn: int) -> int
- optimize(prompt: str, history: list) -> dict
- optimize_prompt(user_prompt, conversation_history, turn_number) -> dict
- save_prompt_event(...) in storage/db.py
- get_recent_history(session_id, limit) in storage/db.py
- upsert_stack_memory(key, value, confidence) in storage/db.py
- get_stack_memory() -> dict[str, str] in storage/db.py
- get_stack_memory_with_confidence() -> list[dict] in storage/db.py
- get_route_stats() -> dict in storage/db.py
- extract_stack_signals(prompt, history) -> dict
- update_memory_from_prompt(prompt, history) -> None
- flush_pending_hook_events() -> int in storage/db.py

## Completed phases
- Phase 1: scaffold, classifier, optimizer, SQLite, MCP server
- Phase 2: hook, Cursor install, CLI commands
- Phase 3: stack memory, extractor, memory-aware optimizer
- Phase 4: GitHub, live test, CONTEXT.md
- Phase 5: session identity, memory consolidation, rich annotations, cross-session history
- Phase 6: packaging, SQLite WAL migration, sidecar concurrency pattern, classifier tuned (38/12), absolute hook path, one-command install, MIT license, distribution README
- Phase 6b: global rename PromptForge → PrePrompt
- Phase 7: GitHub Actions CI + publish workflow, PyPI trusted publisher setup
- Phase 7b: preprompt-optimize CLI command, Windsurf + Zed MCP installers
- Phase 7c: preprompt.skill.md — Claude Skill for tools without MCP support
- Phase 8: activity.log in hook, preprompt-watch live feed, preprompt-clip clipboard optimizer, session summary on server shutdown
- Phase 8b: flush sidecars runs memory extraction (Claude Code → stack memory), watch auto-flushes on startup
- Phase 8c: preprompt-update command, version check on stats/history, version in stats header
- Phase 9: accept/reject tracking, preprompt-feedback CLI, preprompt-install one-command setup, cross-platform clipboard, faster memory (0.85/+0.03), first-run API key wizard, Beehiiv beta signup wired
- Phase 9b: landing page upgrades — social proof strip, FAQ accordion, preprompt-install leads install section, v0.1.5 throughout, mobile ASCII box fixed (CSS card on mobile, full ASCII on desktop), beta signup matches paper/amber design
- Phase 9c: fix get_or_create_session() UNIQUE constraint race — INSERT OR IGNORE + threading.Lock, safe against concurrent Cursor MCP server processes
- Phase 9d: pip-installable hook + registration — cli/hook.py (python -m cli.hook), cli/_register.py (register_hooks()), setup_global_hook.py uses module paths, no __file__-relative paths for installed users
- Phase 9e: maybe_run_setup() wired into all CLI commands (not install_cmd); register_hooks() detects Claude Code + Cursor before writing settings; success message on first-run wizard completion
- Phase 9f: landing page logo + favicon — preprompt-logo-lockup.png in navbar, preprompt-logo-mark.png as favicon (./relative paths for GitHub Pages), ASCII box restored with correct 62-char alignment and proper ╚════╝ footer, font-variant-ligatures:none + tab-size:1 added to .ascii-box CSS
- Phase 9g: intent preservation — expanded _SYSTEM prompt with HARD CONSTRAINTS block (no scope expansion, smallest-safe-fix default, assumption labelling); model configurability via PREPROMPT_MODEL env var (config.py preprompt_model field, getattr fallback in optimizer.py); setup wizard writes commented PREPROMPT_MODEL hint to ~/.preprompt/.env
- Phase 9h: three-route classifier — route_prompt() added to classifier.py (pass/enrich/clarify, pure heuristics); tools.py uses route_prompt, handles clarify route with template-based clarifying questions; storage/db.py gains route column (ALTER TABLE safe for existing DBs); save_prompt_event() gains route kwarg (default "enrich"); all interfaces kept additive
- Phase 9i: classifier scoring fix — replaced blanket "< 6 words: -20" with "< 4 words, no code task, no tech noun: -20"; added _has_technical_noun() (+25 bonus), _CLEAR_ACTION_VERBS (+15 bonus for short specific prompts); _check_clarify Rule 1 guards on not _has_technical_noun so "implement oauth" routes enrich not clarify; 11/11 routing test cases pass
- Phase 9j: clarify mode UX live in both hooks (.claude/hooks/pre_prompt.py and cli/hook.py) — route_prompt() replaces classify_prompt(), clarify route renders CLARIFY annotation box on stderr and prepends clarifying question to prompt, _write_sidecar gains route param (default "enrich")
- Phase 10: local web dashboard — FastAPI server at port 7777 (dashboard/server.py); 5 API endpoints (/api/stats, /api/history, /api/routes, /api/memory, /); single-file SPA with Chart.js doughnut charts, paper/amber design, stats strip, route breakdown, accept/reject chart, history table, stack memory confidence bars, 30s auto-refresh; get_route_stats() and get_stack_memory_with_confidence() added to storage/db.py; get_all_history() default limit raised to 100 and now returns route column; preprompt-dashboard CLI command launches server

## Runtime files
- ~/.preprompt/history.db     — SQLite WAL database
- ~/.preprompt/pending/*.json — hook sidecars (flushed by MCP server)
- ~/.preprompt/activity.log   — plain-text event log (tailed by preprompt-watch)

## PyPI publish instructions
1. Create account at https://pypi.org
2. Go to https://pypi.org/manage/account/publishing/
3. Add trusted publisher: owner=Preprompt-ai, repo=preprompt, workflow=publish.yml, env=release
4. Create GitHub environment "release" at https://github.com/Preprompt-ai/preprompt/settings/environments
5. Tag a release: git tag v0.1.X && git push origin v0.1.X

## Next phases
- Phase 10b: project profiles — separate memory per repo/project
- Phase 10c: before/after diff view in dashboard
- Phase 11: VS Code extension for broader distribution

## Strategic direction
PrePrompt is evolving into two tiers:
- Open source (local-first, silent, pip install, user's own API key, MIT license)
  Open source stays silent — no UI, no dashboard, just the hook working in background
- Hosted paid tier (accounts, hosted optimizer, cloud dashboard, Stripe billing)
  All advanced features live in the paid tier

## Architecture layers
Layer 1: Preflight Engine — BUILT (pass/enrich/clarify, classifier, optimizer)
Layer 2: Memory Layer — PARTIAL (local SQLite, needs project profiles)
Layer 3: Model Layer — PARTIAL (Haiku default, PREPROMPT_MODEL config exists)
Layer 4: Integration Layer — PARTIAL (Claude Code, Cursor, Windsurf, Zed — needs VS Code)
Layer 5: Dashboard — PARTIAL (local only, needs cloud version)
Layer 6: Team Layer — NOT STARTED
Layer 7: Enterprise Layer — NOT STARTED

## Build priority order
1. Demo backend + landing page widget (in progress — feat/demo-backend)
2. Project profiles — separate memory per repo/project
3. VS Code extension — biggest audience unlock
4. Auth system (Supabase auth)
5. Stripe integration + usage metering
6. Cloud dashboard (Supabase Postgres, not local SQLite)
7. Team features
8. Enterprise features (after proven retention)

## GitHub Pages
Landing page: https://preprompt.org
Source: docs/index.html (React JSX via Babel standalone, JetBrains Mono + Inter Tight, paper/amber design system, sections: Nav, Hero, SocialProof, BeforeAfter, HowItWorks, ClassifierTable, StackMemory, Architecture, Cost, Install, FAQ, Beta, Footer)
Logo assets in docs/ (served as GitHub Pages root):
  preprompt-logo-lockup.png     — horizontal lockup used in navbar (height:28px)
  preprompt-logo-mark.png       — icon on dark bg, used as favicon
  preprompt-logo-mark-paper.png — icon on paper bg (spare)

## How new chats should start
User will say "continuing from last chat" or paste this file.
Ask for: cat preprompt/CONTEXT.md
Confirm phase + what comes next, then proceed.

## Environment
- Dev machine: macOS
- API key: in .env as ANTHROPIC_API_KEY
- Claude Code workspace: /Users/user/Documents/Promptforge/promptforge
- GitHub: https://github.com/Preprompt-ai/preprompt

## Current version
v0.1.9 — live on PyPI at https://pypi.org/project/preprompt/

## Distribution
- PyPI: pip install preprompt
- GitHub: github.com/Preprompt-ai/preprompt (public, MIT)
- Landing page: preprompt.org
- Email list: preprompt.beehiiv.com/subscribe

## Known limitations
- Cursor only works in Agent mode — Ask/Plan modes skip MCP tools entirely
- preprompt-watch stats show total=0 when MCP server holds write lock
- Stack memory builds slowly for brand new users (by design — confidence compounds)
- Claude Code hook fires globally across all projects on the machine

## Strategic context
The real opportunity is becoming an AI instruction reliability layer —
checking human intent before it reaches powerful AI agents. Current moat:
cross-tool portability, local-first privacy, user-owned memory, open source.
Not yet VC-ready — need 500+ active installs with retention data first.
Focus: grow individual users, collect accept/reject data proving retry reduction.
