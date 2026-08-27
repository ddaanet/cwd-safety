#!/usr/bin/env python3
"""Dual-mode hook: keep the agent's Bash working directory at project root.

Working-directory drift is when the agent ``cd``s into a subdirectory (often a
git submodule) and then runs later commands from there, silently misleading
itself about project context. Read-only commands from the wrong cwd actively
mislead the agent. This hook makes the project root a hard boundary.

PreToolUse(Bash) — decided against command ``C``, effective root ``R`` (the
enclosing git-worktree root when ``cwd`` is inside a worktree of
``$CLAUDE_PROJECT_DIR``, else ``$CLAUDE_PROJECT_DIR``) and current cwd ``W``
(``cwd`` from hook stdin):

FR3. ``W == R`` and ``C`` is not a ``cd`` command          → allow silently.
FR4. ``C`` is the root-anchored form ``cd R`` or ``cd R && …`` → allow from
   any W (restores cwd and/or runs a command from root).
FR5. Any other leading-``cd`` command (``cd subdir``, ``cd ..``, ``cd -`` …)
   → BLOCK, *even when ``W == R``*. A bare ``cd`` to anywhere but root is the
   most common cause of drift, so it is stopped before it happens. Two shapes
   are rewritten instead of blocked, by appending a ``cd R`` restore line:
   ``cd <dir> && <cmd>`` (FR5a) and a ``set -e``-first script with an embedded
   ``cd`` (FR5c). An embedded ``cd`` after a top-level separator blocks (FR5b).
FR6. ``W != R`` and any other command (drift already happened) → BLOCK with
   instructions to restore via ``cd R``.

PostToolUse(Bash):

FR7. ``W != R`` after a command ran → inject an additionalContext +
   systemMessage warning. ``W == R`` → silent.

Fail-open: if the effective root ``R`` no longer exists on disk (a worktree
removed out from under the session, say), the guard's contract is unsatisfiable.
PreToolUse allows every command silently and PostToolUse swaps the impossible
``cd R`` restore hint for a "guard disabled — restart the session" notice. The
root stays gone, so the guard is effectively off for the rest of the session.

Security: ``cd R && cmd`` is equivalent to running ``cmd`` from project root.
The ``&&`` ensures ``cmd`` runs only if the ``cd`` succeeds. Only ``&&`` is
accepted (not ``;`` or ``||``) to guarantee the cd-first invariant. Exact path
match only — no traversal or normalization.
"""

import json
import os
import re
import shlex
import sys

# A command whose first token is the `cd` builtin: `cd`, `cd …`, `cd;…`, `cd&&…`.
# `cdfoo` does not match (cd must be followed by whitespace, end, or a separator).
_LEADING_CD = re.compile(r"^cd(?:\s|;|&|$)")

# One shell redirection clause that may follow the `cd` target before the `&&`:
# `2>&1`, `>f`, `2>>f`, `<f`, `&>f`, `2>/dev/null`, … A redirection does not change
# the cd-first `&&` semantics, so the root-anchored allow and the FR5a rewrite
# must see past it. Each alternative is either an fd-dup (no filename) or a filename
# token that excludes `& | ; < > ( )` — so nothing here can swallow the `&&`
# separator or smuggle in a second command. `_REDIRS` is zero or more such clauses,
# whitespace-separated. Compact (no literal spaces) so it embeds in both the plain
# and the VERBOSE matcher below.
_REDIR = r"(?:&>>?\s*[^\s&|;<>()]+|\d*(?:>>|>|<)\s*[^\s&|;<>()]+|\d*[<>]&\d*)"
_REDIRS = rf"(?:\s+{_REDIR})*"

# An embedded `cd` that runs in the current shell right after a top-level
# sequencing operator — `mkdir … && cd sub && …`, `echo x; cd sub`. The `cd`
# must *immediately* follow the separator, so a `(cd sub && …)` subshell (a paren
# sits before the `cd`) and a `foo | cd sub` pipeline (single `|`, not matched) are
# never caught. This is a narrow drift detector, not a parser: a quoted literal
# containing `&& cd` is a known, accepted false positive (it only causes a block).
_EMBEDDED_CD = re.compile(r"(?:&&|\|\||;|&|\n)\s*cd(?:\s|;|&|$)")

# Leading blank lines and `#`-comment lines to skip before a command's first
# effective statement (a shebang-like `#!/bin/bash` first line is one such comment).
_LEADING_SKIP = re.compile(r"^(?:[ \t]*(?:#[^\n]*)?\n)*[ \t]*")

# A `set` builtin turning shell errexit *on*: a `-`flag cluster containing `e`
# (`-e`, `-eu`, `-euo`, `-ex`, …) or `-o errexit`. The `+` forms (`set +e`,
# `set +o errexit`) disable it and must not match; the `-` anchors that.
_SET_ERREXIT = re.compile(r"(?:^|\s)-[A-Za-z]*e[A-Za-z]*(?=\s|$)|(?:^|\s)-o\s+errexit(?=\s|$)")

# `cd <dir> && <rest>`: a leading `cd` to a single directory argument, joined by
# `&&` to a non-empty tail. The directory may contain spaces when quoted or
# backslash-escaped, so the argument is matched as one of: a "double-quoted"
# string, a 'single-quoted' string, or a bareword of ordinary chars and `\`-escapes.
# Bare `cd` / `cd && …` (no directory) and the `;`/`||` separators do not match —
# only `&&` preserves the cd-first invariant (see DESIGN decision (c)).
_CD_AND = re.compile(
    rf"""
    ^cd\s+                              # the `cd` builtin and its argument separator
    (                                   # (1) the target directory — one argument:
        "[^"]*"                         #   a "double-quoted" path (may contain spaces)
      | '[^']*'                         #   a 'single-quoted' path (may contain spaces)
      | (?: [^\s'"|&;()<>\\] | \\. )+   #   a bareword: plain chars or `\`-escapes (e.g. `\ `)
    )
    {_REDIRS}                           # optional redirections on the `cd` (`2>&1`, `>f`, …)
    \s*&&\s*                            # the `&&` separator — only `&&`, never `;`/`||`
    \S                                 # a non-empty tail to run after the `cd`
    """,
    re.VERBOSE,
)


def _is_under(path: str, parent: str) -> bool:
    """True if ``path`` equals ``parent`` or sits inside it.

    String containment only — no symlink/realpath resolution (the exact-match
    principle of docs/design.md decision (d) is preserved for the cd match; this is
    only used to confirm a worktree's gitdir belongs to the project).
    """
    parent = parent.rstrip(os.sep)
    return path == parent or path.startswith(parent + os.sep)


def _read_gitdir(dotgit_file: str) -> str:
    """Absolute gitdir path from a worktree ``.git`` file, or "".

    A linked worktree's ``.git`` is a file containing ``gitdir: <path>``.
    Relative paths are resolved against the file's directory.
    """
    try:
        with open(dotgit_file, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return ""
    for line in content.splitlines():
        if line.startswith("gitdir:"):
            path = line[len("gitdir:"):].strip()
            if not path:
                return ""
            if not os.path.isabs(path):
                # Resolved but not normalized; git writes absolute gitdir paths
                # in practice, so a non-normalized relative path simply won't
                # match and the dir is treated as not-a-worktree (safe fallback).
                path = os.path.join(os.path.dirname(dotgit_file), path)
            return path
    return ""


def _worktree_root(cwd: str, project_dir: str) -> str:
    """Enclosing linked-worktree root if ``cwd`` is inside a git worktree of
    ``project_dir``, else "".

    Walks up from ``cwd``; the worktree root is the first ancestor whose
    ``.git`` is a *file* whose ``gitdir:`` resolves under
    ``project_dir/.git``. Stops at ``project_dir`` (the main working tree is
    not a linked worktree) and at the filesystem root. A ``.git`` *directory*
    (a nested main repo) is not a worktree. Filesystem reads only — no
    subprocess. Returns "" on anything unrecognized so the caller falls back
    to ``project_dir``.
    """
    if not cwd or not project_dir:
        return ""
    git_main = os.path.join(project_dir, ".git")
    d = cwd
    while True:
        if d == project_dir:
            return ""
        dotgit = os.path.join(d, ".git")
        if os.path.isfile(dotgit):
            gitdir = _read_gitdir(dotgit)
            if gitdir and _is_under(gitdir, git_main):
                return d
            return ""
        if os.path.isdir(dotgit):
            return ""
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def main() -> None:
    """Dispatch on hook event; the effective root is always allowed.

    The effective root is the enclosing git-worktree root when ``cwd`` is
    inside a worktree of ``$CLAUDE_PROJECT_DIR`` (detected from the on-disk
    ``.git`` linkage), else ``$CLAUDE_PROJECT_DIR``.
    """
    hook_input = json.load(sys.stdin)

    event_name = hook_input.get("hook_event_name", "")
    cwd = hook_input.get("cwd", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")

    # The effective root is the enclosing worktree root when cwd sits inside a
    # worktree of $CLAUDE_PROJECT_DIR, else $CLAUDE_PROJECT_DIR itself.
    worktree = _worktree_root(cwd, project_dir)
    effective_root = worktree or project_dir
    in_worktree = bool(worktree)

    if event_name == "PreToolUse":
        handle_pretooluse(hook_input, cwd, effective_root, in_worktree, project_dir)
    elif event_name == "PostToolUse":
        handle_posttooluse(cwd, effective_root, in_worktree)
    else:
        sys.exit(0)


def _is_cd_to_root(command: str, root: str) -> bool:
    """True if ``command`` is the sanctioned root-anchored form.

    Matches bare ``cd <root>`` and ``cd <root> && <rest>``, with the path
    unquoted, "double"-quoted, or 'single'-quoted, optionally followed by
    redirections (``2>&1``, ``>f`` …) before the ``&&``. Rejects ``;`` and
    ``||`` separators — only ``&&`` preserves the cd-first invariant.
    """
    escaped = re.escape(root)
    pattern = rf"""^cd\s+(?:{escaped}|"{escaped}"|'{escaped}'){_REDIRS}\s*(?:&&\s*.+)?$"""
    return bool(re.match(pattern, command))


def _unquote(arg: str) -> str:
    """One layer of shell de-quoting for a single argument.

    Strips a matching pair of surrounding quotes, else unescapes ``\\x`` -> ``x``.
    Enough to compare a quoted/escaped ``cd`` target to a literal path — not a
    full shell parser.
    """
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        return arg[1:-1]
    return re.sub(r"\\(.)", r"\1", arg)


def _cd_and_target(command: str) -> str:
    """The literal target dir if ``command`` is a ``cd <dir> && <tail>`` form,
    else "".

    Matches the wrappable form (only ``&&``; the dir may be quoted or
    backslash-escaped) and returns the de-quoted directory so callers can
    compare it to a real path. "" means "not a wrappable cd-and command".
    """
    m = _CD_AND.match(command)
    return _unquote(m.group(1)) if m else ""


def _starts_with_errexit(command: str) -> bool:
    """True if ``command``'s first statement is a ``set`` that enables errexit.

    Skips leading blank/``#``-comment lines, then requires the first statement to
    be the ``set`` builtin with errexit turned *on* (``set -e``, ``set -euo
    pipefail``, ``set -ex``, ``set -o errexit`` …). The ``+`` forms (``set +e``)
    disable errexit and do not match; a ``set`` without errexit (``set -u``,
    ``set -o pipefail``, bare ``set``) does not match either. Only the first
    statement's own options are inspected — bounded to the first top-level
    separator. The ``set -e`` is the rewrite's trigger, not a guarantee:
    errexit is inert under the Bash tool, and the appended restore is what
    keeps cwd at the root (see DESIGN FR5c / decision (l),
    docs/references/restore-rewrite.md); ``set`` is matched with ``\\b`` so
    ``setup`` is not mistaken for it.
    """
    head = command[_LEADING_SKIP.match(command).end():]
    m = re.match(r"set\b([^\n;&|]*)", head)
    return bool(m and _SET_ERREXIT.search(m.group(1)))


def _block(message: str) -> None:
    """Deny a PreToolUse command: stderr message, exit 2."""
    sys.stderr.write(message + "\n")
    sys.exit(2)


# FR5a announcements. The rewrite is never silent, but neither channel echoes
# the command: Claude Code already surfaces the rewritten command (updatedInput),
# so echoing it again is bloat. The agent note says cwd was restored, so a
# follow-up that assumed the `cd` persisted is corrected by context.
_REWRITE_AGENT_NOTE = (
    "Appended a `cd` back to the effective root, so your `cd` did not persist "
    "and the working directory is restored. A follow-up command for that "
    "directory needs its own `cd <dir> && <command>`, an absolute path, or "
    "`git -C <dir>`."
)
_REWRITE_USER_NOTE = "Appended a cd back to root."

# FR5c announcements: a `set -e` script with a restore appended. The agent note
# says `set -e` (so a follow-up that assumed cwd persisted is corrected) and,
# like FR5a, does not echo the command — Claude Code surfaces the rewrite. It
# also says that `set -e` is inert here: the Bash tool evals the command as a
# non-final `&&` element, so errexit never aborts the script (decision (l)).
_SET_E_AGENT_NOTE = (
    "Appended a `cd` back to the effective root to your `set -e` script, so any "
    "`cd` inside it did not persist and the working directory is restored. Note "
    "that `set -e` does not abort a Bash tool command; chain with `&&` where a "
    "step must not run after a failure."
)
_SET_E_USER_NOTE = "Appended a cd back to root after set -e script."


def _rewrite_with_restore(
    hook_input: dict, command: str, root: str, agent_note: str, user_note: str
) -> None:
    """Allow ``command`` with a ``cd <root>`` restore line appended, and
    announce the rewrite on both channels. Exits 0.

    The restore is a separate line, not a `( … )` subshell: the sandbox's
    `excludedCommands` matcher only splits `program`/`list`/`pipeline` nodes,
    so a subshell hides the inner command from an exclusion and downgrades it
    to a sandboxed run; and a newline (not `;`) keeps a trailing heredoc or
    comment in ``command`` intact. cwd is therefore restored by a trailing
    statement, not by construction — a tail that `exec`s or `exit`s skips it,
    and PostToolUse (FR7) is the backstop for that.
    The rewrite is never silent (agent: additionalContext; user: systemMessage),
    but the notes do not echo the command — Claude Code already surfaces the
    rewritten `updatedInput`. ``agent_note``/``user_note`` are the channel texts
    for the rewrite kind (FR5a `cd <dir> && <cmd>` vs FR5c `set -e` script).
    """
    new_input = dict(hook_input.get("tool_input", {}))
    new_input["command"] = command + "\ncd " + shlex.quote(root)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "cd back to the effective root appended",
            "additionalContext": agent_note,
            "updatedInput": new_input,
        },
        "systemMessage": user_note,
    }
    print(json.dumps(output))
    sys.exit(0)


def _cd_block_message(root: str, in_worktree: bool) -> str:
    """FR5 block text; worktree-aware when a worktree is active."""
    if in_worktree:
        return (
            "❌ Bash command blocked: `cd` away from the active worktree "
            "causes working-directory drift.\n"
            f"Active worktree root: {root}\n"
            "Instead: use absolute paths or `git -C <dir>`, run from the "
            f"worktree root with `cd {root} && <command>`, or lead with the "
            "`cd <subdir> && <command>` form (a restore is appended for you).\n"
            "To leave the worktree entirely, use the ExitWorktree tool — not `cd`."
        )
    return (
        "❌ Bash command blocked: `cd` away from project root causes "
        "working-directory drift.\n"
        f"Project root: {root}\n"
        "Instead: use absolute paths or `git -C <dir>`, run from root with "
        f"`cd {root} && <command>`, or lead with the "
        "`cd <subdir> && <command>` form (a restore is appended for you)."
    )


def _drift_block_message(cwd: str, root: str, in_worktree: bool) -> str:
    """FR6 block text; worktree-aware when a worktree is active."""
    if in_worktree:
        return (
            "❌ Bash commands blocked: working directory is not the active "
            "worktree root.\n"
            f"Current: {cwd}\n"
            f"Run this command to restore: cd {root}\n"
            "To leave the worktree entirely, use the ExitWorktree tool."
        )
    return (
        "❌ Bash commands blocked: working directory is not project root.\n"
        f"Current: {cwd}\n"
        f"Run this command to restore: cd {root}"
    )


def _drift_warn_message(cwd: str, root: str, in_worktree: bool) -> str:
    """FR7 warning text; worktree-aware when a worktree is active."""
    if in_worktree:
        return (
            f"⚠️  Working directory is not the active worktree root: {cwd}\n"
            "Bash is blocked until cwd is restored.\n"
            f"Run: cd {root}"
        )
    return (
        f"⚠️  Working directory changed to: {cwd}\n"
        "Bash is blocked until cwd is restored.\n"
        f"Run: cd {root}"
    )


def handle_pretooluse(
    hook_input: dict, cwd: str, root: str, in_worktree: bool, project_dir: str
) -> None:
    """Allow root-anchored commands; block drift-inducing cd and wrong-cwd work."""
    # Fail open: if the effective root no longer exists on disk (e.g. a worktree
    # session whose worktree was removed out from under it — the shell then falls
    # back to the main repo), the guard's contract "keep cwd at `root`" is
    # unsatisfiable. Step aside silently so the agent can work from wherever the
    # shell landed. This precedes every rule, so even a leading `cd` is freed —
    # the agent must be able to leave. PostToolUse explains it once per command.
    # After a deletion the root stays gone, so the guard is effectively off for
    # the rest of the session (see DESIGN limitation / decision (k)).
    if root and not os.path.isdir(root):
        sys.exit(0)

    command = hook_input.get("tool_input", {}).get("command", "").strip()

    # FR4: the sanctioned root-anchored form is always allowed.
    if _is_cd_to_root(command, root):
        sys.exit(0)

    # FR5: any other leading `cd` is drift — block it before it happens,
    # even when already at the effective root.
    if _LEADING_CD.match(command):
        # FR5a: at the effective root, a `cd <subdir> && <cmd>` gets a `cd root`
        # restore appended rather than blocked (saves a turn). From a drifted
        # cwd we still block — the agent must restore root first, since the
        # command would run from the wrong cwd.
        # A `cd` to the main project root while a worktree is active is a
        # cross-root transition (governed by ExitWorktree, decision (h)), not a
        # subdir descent — never rewrite it; fall through to the worktree block.
        # The target is compared de-quoted, so a spaced/quoted project-dir path is
        # excluded just as a bare one is.
        target = _cd_and_target(command)
        if target and cwd == root and not (in_worktree and target == project_dir):
            _rewrite_with_restore(
                hook_input, command, root, _REWRITE_AGENT_NOTE, _REWRITE_USER_NOTE
            )
        _block(_cd_block_message(root, in_worktree))

    # FR3: any other command from the effective root is fine — unless it
    # smuggles a drift-inducing `cd` after a top-level separator (`mkdir && cd
    # sub && …`, FR5b). A leading/bare `cd` was already handled by FR5; this
    # catches the embedded case that would otherwise drift and only be caught
    # after the fact by PostToolUse. A `(cd sub && …)` subshell is never matched
    # (the `cd` does not immediately follow the separator). The block reuses the
    # FR5 message, which recommends the leading `cd <subdir> && <command>` form.
    if cwd == root:
        if _EMBEDDED_CD.search(command):
            # FR5c: a `set -e`-first script gets a `cd root` restore appended
            # instead of blocked. The `set -e` is the agent's declared
            # fail-fast intent, but it is inert under the Bash tool (the command
            # is eval'd as a non-final `&&` element), so the restore line — not
            # errexit — is what keeps cwd at root. Fires only here, where an
            # embedded `cd` would otherwise block (FR5b). See decision (l).
            if _starts_with_errexit(command):
                _rewrite_with_restore(
                    hook_input, command, root, _SET_E_AGENT_NOTE, _SET_E_USER_NOTE
                )
            _block(_cd_block_message(root, in_worktree))
        sys.exit(0)

    # FR6: drift already happened — block until cwd is restored.
    _block(_drift_block_message(cwd, root, in_worktree))


def _root_gone_message(root: str) -> str:
    """PostToolUse notice when the effective root no longer exists on disk.

    Replaces the generic drift warning, whose `cd {root}` restore hint is
    impossible once the root is gone. Tells the agent and the human that the
    guard has disabled itself for the rest of the session (see fail-open in
    ``handle_pretooluse``).
    """
    return (
        f"⚠️  cwd-safety: the project root {root} no longer exists — the "
        "working-directory guard is disabled for this session. You are likely "
        "in the main repo now. Restart the session to re-establish a valid root."
    )


def handle_posttooluse(cwd: str, root: str, in_worktree: bool) -> None:
    """Warn (non-blocking) after cwd drift, or when the root has vanished."""
    if root and not os.path.isdir(root):
        # Root deleted — the generic `cd {root}` restore hint is impossible, so
        # emit the fail-open notice instead (guard disabled for the session).
        warning = _root_gone_message(root)
    elif cwd != root:
        warning = _drift_warn_message(cwd, root, in_worktree)
    else:
        sys.exit(0)

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
