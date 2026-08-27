# 2026-07-12 — Redirect tolerance and the narrow embedded-`cd` block (v0.3.1)

Session `5935efe7` — a background worktree session with `$CLAUDE_PROJECT_DIR`
set to the worktree path — put the guard under sustained pressure once its cwd
was deleted, and the agent repeatedly wrote natural command shapes the matchers
mishandled. Three defects were fixed here (`d8b6a3a`); the third was reversed
two days later, see
[2026-07-14 — fail open](2026-07-14-fail-open-deleted-root.md).

## 1. Redirections between the `cd` target and the `&&`

`cd <dir> 2>&1 && <cmd>` — capturing the `cd`'s own diagnostics — was blocked
purely because a redirection sat between the path and the `&&`. A redirection
cannot change the cd-first guarantee `&&` provides (the tail still runs only if
the `cd` succeeds), so tolerating it is free on the security axis. Both the
root-anchored allow and the subshell rewrite now accept them.

The redirection grammar is deliberately bounded — an fd-dup (`2>&1`, no
filename) or a filename token that excludes `& | ; < > ( )` — so it can never
swallow the `&&` or introduce a second command. A non-`&&` separator still
blocks (`cd E 2>&1; <cmd>`), and the exact path match is untouched: the
redirections sit *after* the exactly-matched path.

Allowing arbitrary tokens (not just redirections) before the `&&` was considered
and rejected — junk like `cd E ; rm && cmd` would break the cd-first invariant.

## 2. An embedded `cd` after a top-level separator is blocked

The contrived-`cd R && cd subdir` gap had a common real shape,
`<setup> && cd <subdir> && <work>` (`mkdir -p tools && cd tools && make`), which
drifts from root and was previously caught only after the fact by `PostToolUse`.
A `cd` running in the current shell *immediately* after a top-level sequencing
operator (`&&`, `||`, `;`, `&`, or a newline) is now blocked, with the message
that recommends the `(cd sub && …)` subshell form.

This is a narrow regex, not a shell parser. Requiring the `cd` to *immediately*
follow the separator excludes the sanctioned `(cd sub && …)` subshell and
`foo | cd sub` pipelines for free. The residual false positives — a quoted
`"&& cd"` literal, a no-op `echo x && cd E` — only ever *block*, so the agent
re-forms the command; they cost ergonomics, never safety. The leading
`cd E && cd sub && …` form is deliberately *not* touched: it satisfies the
root-anchored allow by design.

This is a targeted retreat from the "don't chase an exhaustive blocklist"
non-goal, not an abandonment of it — `PostToolUse` remains the backstop for
`pushd`, `source`, an `&& cd` inside a root-anchored command, and everything the
regex cannot see.

Rewriting the embedded `cd` into a subshell instead of blocking was considered
and rejected: correctly splitting a chain around quotes and nested subshells
needs a real parser. Leaving it entirely to `PostToolUse` was also rejected —
the drift executes at least one command from the wrong cwd before the warning
fires.

## 3. `_worktree_main_root` — the CPD-is-worktree guard (since reverted)

In session `5935efe7` the agent ran `cd <main> && git worktree remove --force
<self>` and destroyed its own cwd. The fix at the time was to recognize a
`$CLAUDE_PROJECT_DIR` that is *itself* a linked worktree and treat it as a
worktree — so the `cd` to the main repo was blocked in favor of `ExitWorktree`
rather than subshelled.

That guard was removed on 2026-07-14. `ExitWorktree` is a no-op for a background
worktree session, so the advice was a dead end, and the block forbade the only
working cleanup path. See
[2026-07-14 — fail open on a deleted root](2026-07-14-fail-open-deleted-root.md).

See "Redirect tolerance and the narrow embedded-cd block" in
[design.md](../design.md).
