#!/usr/bin/env python3
"""
Register PrePrompt as an MCP server in Windsurf.

Usage:
    python scripts/install_windsurf.py
"""

import json
import sys
from pathlib import Path


def main() -> None:
    config_path = Path.home() / ".codeium" / "windsurf" / "mcp_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            print(f"Warning: could not parse {config_path} — will overwrite.", file=sys.stderr)

    config.setdefault("mcpServers", {})

    preprompt_py = Path(__file__).parent.parent / ".venv" / "bin" / "preprompt"
    if not preprompt_py.exists():
        preprompt_py = Path(__file__).parent.parent / ".venv" / "Scripts" / "preprompt.exe"

    config["mcpServers"]["preprompt"] = {
        "command": str(preprompt_py),
        "args": [],
        "env": {},
    }

    config_path.write_text(json.dumps(config, indent=2))
    print(f"✓ PrePrompt registered in {config_path}")
    print()
    print("Restart Windsurf to activate the MCP server.")


if __name__ == "__main__":
    main()
