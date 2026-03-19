#!/bin/bash
set -e

echo "Installing PromptForge..."

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

# 5. Register Claude Code global hook
python3 scripts/setup_global_hook.py

# 6. Register Cursor
python3 scripts/install_cursor.py

# 7. Print success
echo ""
echo "PromptForge installed successfully."
echo ""
echo "Restart Claude Code and Cursor to activate."
echo "Test with: promptforge-test-classifier"
