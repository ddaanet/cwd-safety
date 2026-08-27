# 2026-08-27 — Shell-gotchas scan: four fixes and FR7b

A `shell-scripting:shell-gotchas` scan over everything in the plugin that
handles shell text — the matchers and the emitted restore line in
`scripts/cwd-safety.py`, the hook command in `hooks/hooks.json`, and the
`justfile` recipes. Six findings, each reproduced before it was fixed rather
than argued from reading.

## The restore line is separated by a blank line

`_rewrite_with_restore` appended `"\n" + "cd <E>"`. One newline does not end a
statement: a command whose last line ends in a `\` continuation joins to
whatever follows, so

```
cd sub && echo one \
cd /root
```

runs `echo one cd /root` and the restore never executes — verified in bash,
final cwd `sub`. The agent was told "the working directory is restored" by
`_REWRITE_AGENT_NOTE` at the same time, which is worse than the drift alone:
FR7 catches the drift on the next call, but the false reassurance is in context
first.

The separator is now a blank line. The `\` joins to the empty line, the
statement ends, and the `cd` runs. It is inert in every other shape — it lands
after any trailing heredoc's closing delimiter, which is what the flat-line
form exists to protect (decision (i)).

## `_HEREDOC` accepts hyphenated and dotted delimiters

The delimiter word was `[A-Za-z_][A-Za-z0-9_]*`, so `cat > f <<'END-OF'` was
not recognised as a heredoc opener, its body was never masked, and a `cd` line
*inside the body* was read as drift and blocked. Confirmed against the same
command with an `ENDOF` delimiter, which was allowed. Writing a script or doc
whose text contains a `cd` line is exactly the shape `_mask_opaque` exists for.

The word now takes `-` and `.` after its first character. That first character
stays `[A-Za-z_]` deliberately: `_HEREDOC` is tried at every offset regardless
of paren depth, so a digit-initial delimiter would let `$((1 << 3))` open a
heredoc on `3` and blank the rest of a multi-line command. A digit-initial or
space-containing delimiter is the residual, and costs only a spurious block.

## FR7b — no `$CLAUDE_PROJECT_DIR` fails open and shouts

With `$CLAUDE_PROJECT_DIR` unset, `E == ""`, and every rule decides against
`E`. The FR6 drift block therefore fired on *every* Bash command and told the
agent to run:

```
Run this command to restore: cd
```

A `cd` with no argument, which restores nothing. The session was hard-blocked
behind an instruction that cannot be followed — strictly worse than having no
guard. FR7a's fail-open explicitly did not cover this state.

It does now, by the same argument that produced FR7a: the contract "keep cwd at
`E`" is not merely unsatisfiable but unstatable, so the guard steps aside.
`PreToolUse` allows every command; `PostToolUse` emits `_no_root_message` on
both channels, which names `$CLAUDE_PROJECT_DIR` as the fault, says the guard
is disabled for the session, and says outright that nothing runnable from
inside the session repairs it — the variable is set by Claude Code when the
hook fires. Silence would have been wrong: an unset `$CLAUDE_PROJECT_DIR` means
the guard is not running at all, and the human should hear that. Decision (k)
now covers both unusable-root states.

## `hooks.json` quotes the plugin root

`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cwd-safety.py` — Claude Code substitutes
the path textually and the result runs through a shell, so an install path
containing a space word-splits and the hook never starts. Quoted.

## `just probe` stopped rewriting its own payload, and reports blocks

Two defects in the one recipe whose entire purpose is feeding raw shell text to
the matchers.

`--arg c "{{command}}"` is raw textual interpolation inside shell double
quotes, so the probe payload was evaluated by the recipe's own shell before jq
ever saw it:

```
$ just probe PreToolUse <root> 'cd "$(echo INJECTED >&2; echo sub)" && ls'
INJECTED
… "updatedInput": {"command": "cd sub && ls\ncd <root>"}
```

The hook was asked about `cd sub && ls` — a command nobody typed. Any payload
with `"`, `` ` ``, `$` or `\` was silently rewritten, which is most of the
input worth probing. The arguments now go through just's `quote` function.

Separately, `set -euo pipefail` killed the recipe at the `python3` call, so
`echo "exit: $?"` ran only when the exit was 0 and could never print anything
else — a block (exit 2), the outcome most worth probing, aborted the recipe
before it was named. The status is captured explicitly instead.

## Two diagnostics that could lie

`format-docs` derived the wanted rumdl version with `sed -n 's/…/\1/p'`, which
exits 0 on no match; a pin line that changed shape produced an empty `$want`
and the message "pyproject.toml pins " with advice (`uv sync`) that would not
have fixed it. Now checked for emptiness with its own message. And
`check-docs`' link checker read files with a bare `open()`, whose encoding is
locale-dependent; PEP 538 coercion saves plain `LC_ALL=C`, but a genuine
non-UTF-8 locale fails on the `❌` in `design.md`. Pinned to UTF-8.

## Verified clean

`_mask_opaque` held on every realistic shape probed — `grep -n "cd" …`,
`awk "/; cd /…"`, `for…do`, `if…then`, `case …)`, `sed -i.bak`, bare `<<PY`
heredocs — with the `; cd sub` true positive still firing. `_SET_ERREXIT`
correctly rejects `set +e`, `set -o pipefail` and `set --`. `shlex.quote`
handles a spaced root. Every new test was run against the unchanged script
first and failed there.
