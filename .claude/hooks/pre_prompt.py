#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook — thin shim that delegates to cli.hook.
This file exists only for dev-mode compatibility. All logic lives in cli/hook.py.
"""
import sys
import os
from pathlib import Path

# Add project root to path so cli.hook can find mcp_server in dev mode
_HOOK_FILE = os.path.abspath(__file__)
_PROJECT_ROOT = Path(_HOOK_FILE).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cli.hook import main

if __name__ == "__main__":
    main()
