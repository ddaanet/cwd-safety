# 2026-06-12 — `cd <subdir> && <cmd>` is rewritten to a subshell instead of blocked (v0.3.0)

Under the proactive `cd` block, a `cd subdir && cmd` issued from the effective
root was refused — and the block message offered `(cd subdir && cmd)` as the
sanctioned alternative. So the agent spent a full turn reading a block and
reissuing the exact form the hook already knew it wanted. `PreToolUse` hooks can
rewrite the tool input via `hookSpecificOutput.updatedInput`, so the hook can
produce that form itself and save the turn.

At the effective root, `cd <dir> && <rest>` is now allowed with the command
replaced by `(cd <dir> && <rest>)`.

## Why this is free on the security axis

A subshell cannot mutate the parent shell's cwd, so the no-persistence invariant
the whole plugin enforces holds *by construction* — exactly as it would after a
block plus a reissue. This is not the security-for-ergonomics trade that
path normalization was rejected for; nothing about the guarantee weakens.

## The cost that is real, and how it is paid

This is the first behavior that **mutates** the agent's command rather than only
allowing, blocking, or warning. Mutation touches auditability — the executed
command differs from what the transcript shows the agent wrote — and
least-surprise. That is paid down by making the rewrite **never silent**:
`additionalContext` (agent) and `systemMessage` (user) both fire, so neither
party is blind to the substitution, and a follow-up command that assumed
persistence is corrected by context rather than by a confusing wrong-cwd result.
A silent rewrite was considered and rejected outright.

Neither note re-echoes the command — Claude Code already surfaces the rewritten
`updatedInput`, so echoing is bloat. The agent note instead states that cwd did
not persist and recommends the *wrapped* `(cd <dir> && <command>)` form for
follow-ups; recommending the unwrapped form would only trigger another rewrite
and another notification.

## Deliberately narrow

- Fires only from `W == E`. From drift the only sanctioned command is the `cd E`
  restore — a subshell issued from the wrong cwd would run the tail from the
  wrong cwd.
- Requires a single directory argument *and* an `&&` tail. Bare `cd subdir` — the
  persistent-drift *intent* — still hits the block and still teaches. Wrapping it
  would produce the no-op `(cd subdir)` and suppress the lesson.
- Excludes `;` and `||`, for the same reason the root-anchored form does: they
  break the cd-first guarantee.
- While a worktree is active, excludes `cd $CLAUDE_PROJECT_DIR`. That is a
  transition between the two managed roots, governed by `ExitWorktree`, not a
  subdir descent; subshelling it would back-door exactly the cross-root operation
  the single-effective-root decision forbids. The target is compared *de-quoted*,
  so a quoted or escaped main-root path is caught like a bare one.

The `_CD_AND` matcher accepts a directory with spaces when double-quoted,
single-quoted, or backslash-escaped, so real paths like `cd "my dir" && …` are not
needlessly blocked. It is looser than the exact-match root matcher on purpose and
is *not* security-critical: a wrong match can only subshell (still no-persist) or
block (agent reissues). `_is_cd_to_root` is untouched.

See "Rewrite `cd <subdir> && <cmd>` to a subshell instead of blocking" in
[design.md](../design.md).
