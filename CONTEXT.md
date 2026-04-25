<!-- Last updated: 2026-04-25 -->
# PrePrompt — CONTEXT.md
# This file is auto-maintained. Read it fully at the start of every chat.

## Build status
Phase 6 complete. 28/28 tests passing.

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
                   Sidecar pattern: hook writes JSON to ~/.preprompt/pending/,
                   flushed by MCP server or CLI commands via flush_pending_hook_events()
                   get_or_create_session(): stable {hostname}-{date} session key
                   get_all_history(): cross-session history query
                   upsert_stack_memory(): compounding confidence (+0.02/hit, reset on value change)

cli/
  commands.py    — preprompt-history (all sessions), stats, memory,
                   test-classifier, update-context

.claude/
  settings.json           — MCP server + UserPromptSubmit hook config
  hooks/pre_prompt.py     — interception hook with rich box annotation on stderr
                            path resolved via __file__ (CWD-independent)
                            writes JSON sidecar to ~/.preprompt/pending/ — never touches DB directly

scripts/
  install.sh              — one-command installer (Python check, pip, .env, hooks)
  setup_global_hook.py    — global Claude Code MCP + UserPromptSubmit registration
  install_cursor.py       — registers MCP in ~/.cursor/mcp.json
  init_github.py          — git init + first commit + push instructions

LICENSE                   — MIT

tests/
  test_classifier.py    — 12 tests
  test_optimizer.py     —  4 tests
  test_integration.py   — 12 tests

## Key interfaces — never change these signatures
- classify_prompt(prompt: str, history: list, turn: int) -> int
- optimize(prompt: str, history: list) -> dict
- optimize_prompt(user_prompt, conversation_history, turn_number) -> dict
- save_prompt_event(...) in storage/db.py
- get_recent_history(session_id, limit) in storage/db.py
- upsert_stack_memory(key, value, confidence) in storage/db.py
- get_stack_memory() -> dict[str, str] in storage/db.py
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

## Next phases
- Phase 7: PyPI publish (pip install preprompt)
- Phase 7b: preprompt-optimize CLI command + multi-IDE support (Windsurf, Zed)
- Phase 7c (bonus): PrePrompt as a Claude Skill — a .md skill file for tools that don't support MCP hooks

## GitHub Pages
Landing page: https://yashdeeptehlan.github.io/preprompt/
Source: docs/index.html (Tailwind CDN, JetBrains Mono, interactive demo, session replay, FAQ)

## How new chats should start
User will say "continuing from last chat" or paste this file.
Ask for: cat preprompt/CONTEXT.md
Confirm phase + what comes next, then proceed.

## Environment
- Dev machine: macOS
- API key: in .env as ANTHROPIC_API_KEY
- Claude Code workspace: /Users/user/Documents/Promptforge/promptforge
- GitHub: https://github.com/yashdeeptehlan/preprompt
