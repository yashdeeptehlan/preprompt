# PromptForge

> Your prompts, battle-tested. An MCP server that intercepts and optimizes
> prompts in Claude Code and Cursor before they reach the LLM.

## What it does

Most prompts sent to an LLM are underspecified — they're missing context,
output format expectations, or technical constraints that the developer has in
their head but didn't type. PromptForge sits between your keyboard and the
model, scores every prompt with a heuristic classifier, and rewrites the
complex ones using Claude Haiku before the main model ever sees them. Simple
prompts ("what is jwt") pass through untouched in under 1ms. No API cost, no
latency, no noise.

## How it works

```
BEFORE  write a function that handles youtube oauth token refresh
        and manages expired credentials with error handling

AFTER   Write a Python function for FastAPI that handles YouTube OAuth 2.0
        token refresh. The function should: (1) detect expired credentials
        by checking the expiry timestamp, (2) use the refresh token to
        obtain a new access token via the YouTube API, (3) update and
        persist the new credentials (consider using a database or file
        storage), and (4) include comprehensive error handling for invalid
        refresh tokens, network failures, and API errors with appropriate
        logging and exception types.
```

When a prompt is intercepted you see this in your terminal:

```
╔═ PromptForge +58 ══════════════════════════════════════════╗
║ The rewritten prompt specifies the technical               ║
║ implementation details, clarifies the complete workflow,   ║
║ and adds concrete error scenarios and storage              ║
║ considerations relevant to FastAPI applications.           ║
╠════════════════════════════════════════════════════════════╣
║ ORIGINAL  write a function that handles youtube oauth t... ║
║ OPTIMIZED Write a Python function for FastAPI that handles ║
║           YouTube OAuth 2.0 token refresh. The function    ║
║           should: (1) detect expired credentials by        ║
║           checking the expiry timestamp...                 ║
╚════════════════════════════════════════════════════════════╝
```

## Install

```bash
git clone https://github.com/yashdeeptehlan/promptforge
cd promptforge
./scripts/install.sh
```

Or manually:

```bash
pip install -e .
cp .env.example .env    # add your ANTHROPIC_API_KEY
python scripts/setup_global_hook.py
python scripts/install_cursor.py
```

## How the smart classifier works

- **Pure heuristics, zero API calls** — runs on every prompt in under 1ms
- Scores each prompt based on: ambiguity verbs, multi-requirement density,
  turn depth, and missing output format signals
- **Only intercepts when score ≥ 45** — simple prompts always pass through
- Negative signals: short prompts, lookup questions (`what is`, `what does`),
  already-structured prompts all score low and get skipped

```
SCORE  INTERCEPT  PROMPT
   48  YES        write me a middleware that validates tokens and handles refresh
  -35  no         what is jwt
  -45  no         thanks
   65  YES        refactor this to handle edge cases and manage errors properly
  -20  no         add tests
   70  YES        implement a rate limiter that tracks requests, manages quotas...
```

## Stack memory

PromptForge learns your stack as you work. After a few sessions it knows your
language, framework, and style preferences and injects that context into every
optimization automatically.

```
$ promptforge-memory
 PromptForge — learned stack memory
──────────────────────────────────────────────────────
  language     python           confidence: 0.92  (seen 47x)
  framework    fastapi          confidence: 0.88  (seen 31x)
  database     postgresql       confidence: 0.74  (seen 12x)
```

## CLI

```bash
promptforge-history          # recent prompt events across all sessions
promptforge-stats            # optimization stats (total, intercepted, avg score)
promptforge-memory           # learned stack context
promptforge-test-classifier  # test classifier on sample prompts
```

## Cost

PromptForge uses `claude-haiku-4-5` for optimization — the cheapest Claude
model. Typical cost: ~$0.001 per intercepted prompt. At 20 complex prompts
per day that's roughly **$0.60/month**. Simple prompts are never sent to the
API.

## Architecture

```
Claude Code / Cursor
        │
        ▼  UserPromptSubmit hook
  pre_prompt.py
  ├── classify_prompt()     ← pure heuristic, <1ms, no API
  ├── [score < 45] ──────► pass through unchanged
  └── [score ≥ 45]
      ├── optimize()        ← Haiku API call with stack context
      ├── write sidecar     ← ~/.promptforge/pending/<uuid>.json
      └── return optimized prompt
        │
        ▼  MCP server (on next tool call)
  flush_pending_hook_events()  ← sidecars → DuckDB
  save_prompt_event()
  update_memory_from_prompt()
```

The hook never holds a database connection — it writes a small JSON sidecar
file so there's no lock conflict with the MCP server.

## MCP Tools

| Tool | Parameters | Returns |
|------|-----------|---------|
| `optimize_prompt` | `user_prompt`, `conversation_history`, `turn_number` | `{optimized_prompt, was_intercepted, score, reason}` |
| `get_prompt_history` | `limit` (default 20) | list of recent prompt events for this session |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required for optimizer |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |

Storage is always at `~/.promptforge/history.db` (created automatically).

## Requirements

- Python 3.11+
- Anthropic API key (get one at [console.anthropic.com](https://console.anthropic.com))
- Claude Code and/or Cursor

## License

MIT — see [LICENSE](LICENSE)
