#!/usr/bin/env python3
"""
Register PrePrompt as an MCP server in Zed.

Usage:
    python scripts/install_zed.py
"""

import json
import sys
from pathlib import Path


def main() -> None:
    settings_path = Path.home() / ".config" / "zed" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            print(f"Warning: could not parse {settings_path} — will overwrite.", file=sys.stderr)

    settings.setdefault("context_servers", {})

    preprompt_py = Path(__file__).parent.parent / ".venv" / "bin" / "preprompt"
    if not preprompt_py.exists():
        preprompt_py = Path(__file__).parent.parent / ".venv" / "Scripts" / "preprompt.exe"

    settings["context_servers"]["preprompt"] = {
        "command": {
            "path": str(preprompt_py),
            "args": [],
        }
    }

    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"✓ PrePrompt registered in {settings_path}")
    print()
    print("Restart Zed to activate the MCP server.")


if __name__ == "__main__":
    main()
