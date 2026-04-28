#!/bin/bash
set -e

echo "Installing PrePrompt..."

# 1. Check Python >= 3.11
python3 --version | grep -E "3\.(1[1-9]|[2-9][0-9])" || {
    echo "Error: Python 3.11+ required"
    exit 1
}

# 2. Install the package
pip install -e "." --quiet

# 3. Prompt for API key if not set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "Enter your Anthropic API key (get one at console.anthropic.com):"
    read -s ANTHROPIC_API_KEY
fi

# 4. Write .env file
echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" > .env
echo "MCP_TRANSPORT=stdio" >> .env

# 5. Migrate old DuckDB file + register Claude Code global hook
# (setup_global_hook.py handles the migration automatically)
python3 scripts/setup_global_hook.py

# 6. Register Cursor
python3 scripts/install_cursor.py

# 7. Register Windsurf (if installed)
if [ -d "$HOME/.codeium/windsurf" ]; then
    python3 scripts/install_windsurf.py
fi

# 8. Register Zed (if installed)
if [ -d "$HOME/.config/zed" ]; then
    python3 scripts/install_zed.py
fi

# 9. Print success
echo ""
echo "PrePrompt installed successfully."
echo ""
echo "Restart Claude Code, Cursor, Windsurf, or Zed to activate."
echo "Test with: preprompt-test-classifier"
