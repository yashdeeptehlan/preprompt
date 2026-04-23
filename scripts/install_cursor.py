#!/usr/bin/env python3
"""
One-time setup: register PrePrompt in Cursor's MCP config.
Safe to run multiple times — upserts the "preprompt" entry.

Usage:
    python scripts/install_cursor.py
"""

import json
import os
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).parent.parent.resolve()

    # ── Load ANTHROPIC_API_KEY from .env ──────────────────────────────────────
    env_file = repo_root / ".env"
    api_key = ""
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    # ── Read or initialise Cursor's mcp.json ──────────────────────────────────
    cursor_config_path = Path.home() / ".cursor" / "mcp.json"
    cursor_config_path.parent.mkdir(parents=True, exist_ok=True)

    if cursor_config_path.exists():
        try:
            with open(cursor_config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            config = {}
    else:
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # ── Upsert the preprompt entry ────────────────────────────────────────────
    config["mcpServers"]["preprompt"] = {
        "command": "python",
        "args": ["-m", "mcp_server.server"],
        "cwd": str(repo_root),
        "env": {
            "ANTHROPIC_API_KEY": api_key,
            "PYTHONPATH": str(repo_root),
        },
    }

    with open(cursor_config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print("✓ PrePrompt registered in ~/.cursor/mcp.json")
    print("↻ Restart Cursor for changes to take effect")
    if not api_key:
        print("⚠ ANTHROPIC_API_KEY not found in .env — add it before restarting")


if __name__ == "__main__":
    main()
