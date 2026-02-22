#!/bin/bash
set -e

TARGET="$HOME/.claude/commands/spiritbox.md"

if [ -f "$TARGET" ]; then
    rm "$TARGET"
    echo "Removed: $TARGET"
else
    echo "Not installed: $TARGET"
fi
