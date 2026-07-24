import 'plugin-dev/release.just'

# cwd-safety plugin — dev recipes

# Default: list recipes
_default:
    @just --list

# Lint manifests + hooks.json, byte-compile the hook, run the test suite.
precommit:
    jq . .claude-plugin/plugin.json > /dev/null
    jq . hooks/hooks.json > /dev/null
    python3 -m py_compile scripts/cwd-safety.py tests/test_cwd_safety.py
    python3 tests/test_cwd_safety.py
    @echo "ok"

# The gate `release` depends on; identical to precommit for this plugin.
prerelease: precommit

# Run the hook test suite.
test:
    python3 tests/test_cwd_safety.py

# Drive the hook once by hand from a chosen cwd.
#   just probe PreToolUse /some/cwd 'cd subdir && ls'
# CLAUDE_PROJECT_DIR defaults to this repo root.
probe event cwd command='':
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(git rev-parse --show-toplevel)"
    json=$(jq -cn --arg e "{{event}}" --arg w "{{cwd}}" --arg c "{{command}}" \
        '{hook_event_name:$e, cwd:$w, tool_input:{command:$c}}')
    printf '%s' "$json" | CLAUDE_PROJECT_DIR="$root" python3 scripts/cwd-safety.py
    echo "exit: $?"
