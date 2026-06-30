#!/bin/sh
set -e

# 1. Create workspace directories before Python imports providers.py
#    (SqliteCircuitBreakerStore init reads PATCHFORGE_DATA_DIR at import time)
mkdir -p /workspace/runs /workspace/logs /workspace/stores

# 2. Ensure HOME is writable (supports docker run --user with arbitrary UIDs).
#    When --user NNNN:NNNN maps to a UID that doesn't own /home/patchforge,
#    redirect HOME to /tmp so git config --global can write .gitconfig there.
if [ ! -w "$HOME" ]; then
    export HOME=/tmp/patchforge-home
fi
mkdir -p "$HOME"

# 3. Git safe.directory — mounted repos are owned by host UID
git config --global --add safe.directory /repo

# 4. Git identity for apply (commits) and validation_workspace (temp git init)
git config --global user.name "patchforge[bot]"
git config --global user.email "patchforge@users.noreply.github.com"

# 5. Git credential helper for push operations
TOKEN="${PATCHFORGE_GITHUB_TOKEN:-$GITHUB_TOKEN}"
if [ -n "$TOKEN" ]; then
    git config --global url."https://x-access-token:${TOKEN}@github.com/".insteadOf "https://github.com/"
fi

# 6. Validate API keys (skip for commands that don't need them)
NEEDS_KEYS=true
for arg in "$@"; do
    case "$arg" in
        --help|-h) NEEDS_KEYS=false; break ;;
        doctor|scan) NEEDS_KEYS=false; break ;;
    esac
done

if [ "$NEEDS_KEYS" = true ]; then
    if [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ]; then
        echo "ERROR: No LLM API key configured." >&2
        echo "Set at least one of: ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENROUTER_API_KEY" >&2
        exit 1
    fi
fi

# 7. Replace shell with patchforge process (proper signal propagation)
exec "$@"
