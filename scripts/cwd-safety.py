#!/usr/bin/env python3
"""Dual-mode hook: keep the agent's Bash working directory at project root.

Working-directory drift is when the agent ``cd``s into a subdirectory (often a
git submodule) and then runs later commands from there, silently misleading
itself about project context. Read-only commands from the wrong cwd actively
mislead the agent. This hook makes the project root a hard boundary.

PreToolUse(Bash) — decided against command ``C``, project root ``R``
(``$CLAUDE_PROJECT_DIR``) and current cwd ``W`` (``cwd`` from hook stdin):

1. ``W == R`` and ``C`` is not a ``cd`` command            → allow silently.
2. ``C`` is the root-anchored form ``cd R`` or ``cd R && …``→ allow from any W
   (restores cwd and/or runs a command from root).
3. Any other leading-``cd`` command (``cd subdir``, ``cd ..``, ``cd -`` …)
   → BLOCK, *even when ``W == R``*. A bare ``cd`` to anywhere but root is the
   most common cause of drift, so it is stopped before it happens.
4. ``W != R`` and any other command (drift already happened) → BLOCK with
   instructions to restore via ``cd R``.

PostToolUse(Bash):

5. ``W != R`` after a command ran → inject an additionalContext + systemMessage
   warning. ``W == R`` → silent.

Security: ``cd R && cmd`` is equivalent to running ``cmd`` from project root.
The ``&&`` ensures ``cmd`` runs only if the ``cd`` succeeds. Only ``&&`` is
accepted (not ``;`` or ``||``) to guarantee the cd-first invariant. Exact path
match only — no traversal or normalization.
"""

import json
import os
import re
import sys

# A command whose first token is the `cd` builtin: `cd`, `cd …`, `cd;…`, `cd&&…`.
# `cdfoo` does not match (cd must be followed by whitespace, end, or a separator).
_LEADING_CD = re.compile(r"^cd(?:\s|;|&|$)")


def main() -> None:
    """Dispatch on hook event; the effective root is always allowed.

    The effective root is the active worktree root when Claude Code reports
    one (the ``worktree`` field in the hook payload), else ``$CLAUDE_PROJECT_DIR``.
    """
    hook_input = json.load(sys.stdin)

    event_name = hook_input.get("hook_event_name", "")
    cwd = hook_input.get("cwd", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    worktree = hook_input.get("worktree") or ""  # absent / null / "" → no worktree
    effective_root = worktree or project_dir
    in_worktree = bool(worktree)

    if event_name == "PreToolUse":
        handle_pretooluse(hook_input, cwd, effective_root, in_worktree)
    elif event_name == "PostToolUse":
        handle_posttooluse(cwd, effective_root, in_worktree)
    else:
        sys.exit(0)


def _is_cd_to_root(command: str, root: str) -> bool:
    """True if ``command`` is the sanctioned root-anchored form.

    Matches bare ``cd <root>`` and ``cd <root> && <rest>``, with the path
    unquoted, "double"-quoted, or 'single'-quoted. Rejects ``;`` and ``||``
    separators — only ``&&`` preserves the cd-first invariant.
    """
    escaped = re.escape(root)
    pattern = rf"""^cd\s+(?:{escaped}|"{escaped}"|'{escaped}')\s*(?:&&\s*.+)?$"""
    return bool(re.match(pattern, command))


def _block(message: str) -> None:
    """Deny a PreToolUse command: stderr message, exit 2."""
    sys.stderr.write(message + "\n")
    sys.exit(2)


def _cd_block_message(root: str, in_worktree: bool) -> str:
    """Rule 3 block text (worktree-aware variant added in Task 3)."""
    return (
        "❌ Bash command blocked: `cd` away from project root causes "
        "working-directory drift.\n"
        f"Project root: {root}\n"
        "Instead: use absolute paths, run from root with "
        f"`cd {root} && <command>`, or scope the change to a "
        "subshell that does not persist, e.g. `(cd subdir && <command>)`."
    )


def _drift_block_message(cwd: str, root: str, in_worktree: bool) -> str:
    """Rule 4 block text (worktree-aware variant added in Task 3)."""
    return (
        "❌ Bash commands blocked: working directory is not project root.\n"
        f"Current: {cwd}\n"
        f"Run this command to restore: cd {root}"
    )


def _drift_warn_message(cwd: str, root: str, in_worktree: bool) -> str:
    """Rule 5 warning text (worktree-aware variant added in Task 3)."""
    return (
        f"⚠️  Working directory changed to: {cwd}\n"
        "Bash is blocked until cwd is restored.\n"
        f"Run: cd {root}"
    )


def handle_pretooluse(hook_input: dict, cwd: str, root: str, in_worktree: bool) -> None:
    """Allow root-anchored commands; block drift-inducing cd and wrong-cwd work."""
    command = hook_input.get("tool_input", {}).get("command", "").strip()

    # Rule 2: the sanctioned root-anchored form is always allowed.
    if _is_cd_to_root(command, root):
        sys.exit(0)

    # Rule 3: any other leading `cd` is drift — block it before it happens,
    # even when already at the effective root.
    if _LEADING_CD.match(command):
        _block(_cd_block_message(root, in_worktree))

    # Rule 1: any other command from the effective root is fine.
    if cwd == root:
        sys.exit(0)

    # Rule 4: drift already happened — block until cwd is restored.
    _block(_drift_block_message(cwd, root, in_worktree))


def handle_posttooluse(cwd: str, root: str, in_worktree: bool) -> None:
    """Warn (non-blocking) after cwd drift is detected."""
    if cwd == root:
        sys.exit(0)

    warning = _drift_warn_message(cwd, root, in_worktree)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": warning,
        },
        "systemMessage": warning,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
