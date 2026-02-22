#!/bin/bash
set -e

COMMANDS_DIR="$HOME/.claude/commands"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$COMMANDS_DIR"
cp "$SCRIPT_DIR/.claude/commands/spiritbox.md" "$COMMANDS_DIR/spiritbox.md"

echo "Installed: $COMMANDS_DIR/spiritbox.md"
echo ""
echo "Usage: /spiritbox path/to/deck.pptx"
echo ""
echo "Note: spiritbox Python package must also be installed:"
echo "  pip install -e $(dirname "$0")/"
