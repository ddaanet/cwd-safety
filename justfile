import 'plugin-dev/release.just'

# cwd-safety plugin — dev recipes

# Default: list recipes
_default:
    @just --list

# Lint manifests + hooks.json, byte-compile the hook, run the test suite,
# rewrap the docs and check the docs graph.
precommit: format-docs check-docs
    jq . .claude-plugin/plugin.json > /dev/null
    jq . hooks/hooks.json > /dev/null
    python3 -m py_compile scripts/cwd-safety.py tests/test_cwd_safety.py
    python3 tests/test_cwd_safety.py
    @echo "ok"

# Hard-wraps prose in docs/ and plans/ so a line count means something. rumdl
# comes from uv.lock via `uv sync`, on PATH through `.envrc`; the pin check
# turns a stale `.venv` into a message instead of a differently wrapped tree.
format-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    have=$({{ rumdl }} --version) || { echo "format-docs: rumdl not on PATH — run 'uv sync' and let direnv load .envrc" >&2; exit 1; }
    want=$(sed -n 's/.*"rumdl==\([0-9.]*\)".*/\1/p' pyproject.toml)
    [ "$have" = "rumdl $want" ] || { echo "format-docs: $have on PATH, pyproject.toml pins $want — run 'uv sync'" >&2; exit 1; }
    {{ rumdl }} fmt --no-cache docs plans

# Overridable so a test can stand in a stub: `just rumdl=/path/to/stub format-docs`.
rumdl := "rumdl"

# The docs graph: the hub `docs/design.md` and every `docs/references/` node
# stay under the line cap a reader loads in one go, and every relative
# markdown link in `docs/` resolves. Line counts are only meaningful after
# `format-docs`, which is why precommit runs it first.
check-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    shopt -s nullglob
    cap=400
    status=0
    for f in docs/design.md docs/references/*.md; do
        n=$(wc -l < "$f")
        if [ "$n" -gt "$cap" ]; then
            echo "check-docs: $f is $n lines, cap is $cap — move a mechanism into docs/references/ (see docs/design.md, top)" >&2
            status=1
        fi
    done
    python3 - <<'PY' || status=1
    import os, re, sys
    bad = 0
    for root, _, files in os.walk("docs"):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            text = re.sub(r"```.*?```", "", open(path).read(), flags=re.S)
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
                if "://" in target:
                    continue
                if not os.path.exists(os.path.join(root, target)):
                    print(f"check-docs: {path}: broken link {target}", file=sys.stderr)
                    bad += 1
    sys.exit(1 if bad else 0)
    PY
    exit "$status"

# The gate `release` depends on; identical to precommit for this plugin.
prerelease: precommit

# Run the hook test suite.
test:
    python3 tests/test_cwd_safety.py

# Drive the hook once by hand from a chosen cwd.
probe event cwd command='':
    #!/usr/bin/env bash
    # just probe PreToolUse /some/cwd 'cd subdir && ls'
    # CLAUDE_PROJECT_DIR defaults to this repo root.
    set -euo pipefail
    root="$(git rev-parse --show-toplevel)"
    json=$(jq -cn --arg e "{{event}}" --arg w "{{cwd}}" --arg c "{{command}}" \
        '{hook_event_name:$e, cwd:$w, tool_input:{command:$c}}')
    printf '%s' "$json" | CLAUDE_PROJECT_DIR="$root" python3 scripts/cwd-safety.py
    echo "exit: $?"
