<!-- Last updated: 2026-03-19 02:57 -->
# PromptForge — CONTEXT.md
# This file is auto-maintained. Read it fully at the start of every chat.

## Build status
Phase 5 complete. 28/28 tests passing.

## What PromptForge does
MCP server that intercepts prompts in Claude Code and Cursor, scores them
with a heuristic classifier (no API), optimizes complex ones using Claude
Haiku, logs everything to DuckDB, and learns the user's stack over time
via a memory layer. Runs entirely locally.

## Tech stack
- Python 3.11+, FastMCP, Anthropic SDK, DuckDB, pydantic-settings
- Haiku model: claude-haiku-4-5-20251001
- DB path: ~/.promptforge/history.db

## File map
mcp_server/
  server.py      — entry point, mcp.run(transport=settings.mcp_transport)
  tools.py       — MCP tools: optimize_prompt, get_prompt_history
                   uses get_or_create_session() for stable daily session identity
  classifier.py  — pure heuristic scorer, threshold=45, no API calls
  optimizer.py   — Haiku API call + memory context injection
                   strips markdown fences from model JSON response
  extractor.py   — heuristic stack signal extractor
  config.py      — pydantic-settings, reads .env

storage/
  db.py          — DuckDB: prompt_history + stack_memory + sessions tables
                   get_or_create_session(): stable {hostname}-{date} session key
                   get_all_history(): cross-session history query
                   upsert_stack_memory(): compounding confidence (+0.02/hit, reset on value change)

cli/
  commands.py    — promptforge-history (all sessions), stats, memory,
                   test-classifier, update-context

.claude/
  settings.json           — MCP server + UserPromptSubmit hook config
  hooks/pre_prompt.py     — interception hook with rich box annotation on stderr
                            path resolved via __file__ (CWD-independent)

scripts/
  install_cursor.py   — registers MCP in ~/.cursor/mcp.json
  init_github.py      — git init + first commit + push instructions

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

## Completed phases
- Phase 1: scaffold, classifier, optimizer, DuckDB, MCP server
- Phase 2: hook, Cursor install, CLI commands
- Phase 3: stack memory, extractor, memory-aware optimizer
- Phase 4: GitHub, live test, CONTEXT.md
- Phase 5: session identity, memory consolidation, rich annotations, cross-session history

## Next phases
- Phase 6: packaging for distribution (pip install promptforge)

## How new chats should start
User will say "continuing from last chat" or paste this file.
Ask for: cat promptforge/CONTEXT.md
Confirm phase + what comes next, then proceed.

## Environment
- Dev machine: macOS
- API key: in .env as ANTHROPIC_API_KEY
- Claude Code workspace: /Users/user/Documents/Promptforge/promptforge
- GitHub: https://github.com/yashdeeptehlan/promptforge
