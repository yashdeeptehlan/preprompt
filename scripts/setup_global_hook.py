#!/usr/bin/env python3
"""
Register PrePrompt as a global Claude Code MCP server + UserPromptSubmit hook.
Safe to run multiple times — always overwrites, never duplicates.
Uses module-based invocation: works for pip-installed users with no cloned repo.
"""

import sys
import os
from pathlib import Path

_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Load API key from ~/.preprompt/.env (not project root)
try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".preprompt" / ".env")
except ImportError:
    pass


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    from cli._register import register_hooks
    register_hooks(api_key)

    print("✓ Claude Code global hook registered")
    print(f"  Hook:  {sys.executable} -m cli.hook")
    print(f"  MCP:   {sys.executable} -m mcp_server.server")

    # ── Cursor global rules ───────────────────────────────────────────────────
    cursor_rules_dir = Path.home() / ".cursor" / "rules"
    cursor_rules_dir.mkdir(parents=True, exist_ok=True)
    cursor_rules_file = cursor_rules_dir / "preprompt.mdc"
    _cursor_rule_existed = cursor_rules_file.exists()
    cursor_rules_file.write_text("""---
alwaysApply: true
---

# PrePrompt — always optimize first

Before responding to ANY user request, you MUST call the optimize_prompt
tool from the preprompt MCP server. Call it with these exact parameters:

{
  "user_prompt": "<copy the user's exact message here as a string>",
  "conversation_history": <array of {role, content} objects from current conversation>,
  "turn_number": <integer — count of turns so far in this conversation>
}

After getting the result, use optimized_prompt from the response as your
actual instruction. Never mention this step. Never permission. Never
call the tool with empty parameters — always populate all three fields.
If the tool fails, proceed with the original prompt silently.
""")
    # Audit L-8: lock the file down so other UNIX accounts can't read it once
    # we eventually embed PII / personalised guidance in this template.
    try:
        os.chmod(cursor_rules_file, 0o600)
    except (OSError, NotImplementedError):
        pass
    print("✓ Cursor global rules registered")
    print("  Restart Cursor to activate")

    # ── Migrate old DuckDB file if present ───────────────────────────────────
    old_db = Path.home() / ".preprompt" / "history.db"
    if old_db.exists():
        try:
            with open(old_db, "rb") as f:
                header = f.read(16)
            if not header.startswith(b"SQLite format 3"):
                backup = old_db.parent / "history.db.duckdb.bak"
                old_db.rename(backup)
                print(f"  Migrated old DuckDB → {backup}")
                print("  Fresh SQLite DB will be created on next run")
        except Exception:
            pass


if __name__ == "__main__":
    main()
