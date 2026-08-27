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
    # sed exits 0 on no match, so an empty $want means the pin line changed
    # shape — not a stale venv, and 'uv sync' would not fix it.
    [ -n "$want" ] || { echo "format-docs: no \"rumdl==<version>\" pin found in pyproject.toml" >&2; exit 1; }
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
            raw = open(path, encoding="utf-8").read()  # docs carry ❌/⚠️ literals
            text = re.sub(r"```.*?```", "", raw, flags=re.S)
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
    # The arguments below go through just's `quote` function, never bare inside
    # shell double quotes: just interpolates raw text, so a double-quoted
    # interpolation hands the probe payload to *this* shell first — a `$(…)` in
    # it runs, quotes collapse, and the hook is then asked about a command
    # nobody typed. The payload is shell text by definition; it must reach jq
    # byte for byte.
    set -uo pipefail
    root="$(git rev-parse --show-toplevel)" || exit 1
    json=$(jq -cn --arg e {{ quote(event) }} --arg w {{ quote(cwd) }} --arg c {{ quote(command) }} \
        '{hook_event_name:$e, cwd:$w, tool_input:{command:$c}}') || exit 1
    # No `set -e`, and the status is captured: a block is exit 2, the outcome
    # most worth probing, and errexit would kill the recipe before it is named.
    printf '%s' "$json" | CLAUDE_PROJECT_DIR="$root" python3 scripts/cwd-safety.py
    rc=$?
    echo "exit: $rc"
