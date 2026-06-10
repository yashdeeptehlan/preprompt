"""
Register PrePrompt hooks in ~/.claude/settings.json.
Called from install_cmd(), update_cmd(), and maybe_run_setup().
Uses module-based invocation so it works for pip-installed users with no cloned repo.
"""
import sys
import json
import os
import shutil
from pathlib import Path

_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def register_hooks(api_key: str = "") -> None:
    """Write preprompt MCP server + UserPromptSubmit hook to global Claude Code settings."""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    claude_available = shutil.which("claude") is not None
    cursor_available = (Path.home() / ".cursor").exists() or shutil.which("cursor") is not None

    if not claude_available and not cursor_available:
        print("  No IDE detected. Install Claude Code or Cursor then run preprompt-install again.")
        return

    if _SETTINGS_PATH.exists():
        try:
            settings = json.loads(_SETTINGS_PATH.read_text())
        except Exception:
            settings = {}
    else:
        settings = {}

    if claude_available:
        settings.setdefault("mcpServers", {})
        settings["mcpServers"]["preprompt"] = {
            "command": sys.executable,
            "args": ["-m", "mcp_server.server"],
            "cwd": str(Path.home()),
            "env": {"ANTHROPIC_API_KEY": api_key},
        }

        settings.setdefault("hooks", {})
        settings["hooks"]["UserPromptSubmit"] = [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{sys.executable} -m cli.hook",
                    }
                ],
            }
        ]

        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
        # Audit L-9: settings.json holds the Anthropic API key. Restrict reads
        # to the owning user so other accounts on a shared box can't grab it.
        try:
            os.chmod(_SETTINGS_PATH, 0o600)
        except (OSError, NotImplementedError):
            pass
    else:
        print("  Claude Code not detected — skipping Claude Code hook")

    if not cursor_available:
        print("  Cursor not detected — skipping Cursor registration")
