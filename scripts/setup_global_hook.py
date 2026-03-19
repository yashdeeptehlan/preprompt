#!/usr/bin/env python3
"""
Register PromptForge as a global Claude Code MCP server + UserPromptSubmit hook.

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
    settings["mcpServers"]["promptforge"] = {
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


if __name__ == "__main__":
    main()
