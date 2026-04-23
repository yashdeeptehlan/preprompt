#!/usr/bin/env python3
"""
Register PrePrompt as a global Claude Code MCP server + UserPromptSubmit hook.

Safe to run multiple times — always overwrites, never duplicates.
"""

import json
import sys
from pathlib import Path

# Resolve project root from this file's location (scripts/ -> project root)
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_HOOK_PATH = _PROJECT_ROOT / ".claude" / "hooks" / "pre_prompt.py"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Load API key from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

import os
_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def _load_settings() -> dict:
    if _SETTINGS_PATH.exists():
        try:
            return json.loads(_SETTINGS_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_settings(settings: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")


def main() -> None:
    settings = _load_settings()

    # ── MCP server entry ──────────────────────────────────────────────────────
    settings.setdefault("mcpServers", {})
    settings["mcpServers"]["preprompt"] = {
        "command": "python",
        "args": ["-m", "mcp_server.server"],
        "cwd": str(_PROJECT_ROOT),
        "env": {"ANTHROPIC_API_KEY": _API_KEY},
    }

    # ── UserPromptSubmit hook entry ───────────────────────────────────────────
    settings.setdefault("hooks", {})
    settings["hooks"]["UserPromptSubmit"] = [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python3 {_HOOK_PATH}",
                }
            ],
        }
    ]

    _save_settings(settings)

    print("✓ Claude Code global hook registered")
    print(f"  Hook: {_HOOK_PATH}")
    print(f"  MCP:  {_PROJECT_ROOT}")

    # ── Cursor global rules ───────────────────────────────────────────────────
    cursor_rules_dir = Path.home() / ".cursor" / "rules"
    cursor_rules_dir.mkdir(parents=True, exist_ok=True)
    cursor_rules_file = cursor_rules_dir / "preprompt.mdc"
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
    print("✓ Cursor global rules registered")
    print("  Restart Cursor to activate")

    # ── Migrate old DuckDB file if present ───────────────────────────────────
    old_db = Path.home() / ".preprompt" / "history.db"
    if old_db.exists():
        try:
            # Quick check: SQLite files start with "SQLite format 3"
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
