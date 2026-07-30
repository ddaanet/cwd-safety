# 2026-07-14 — Fail open on a deleted root; drop the shape-2 self-destruct guard (v0.3.1)

Reading the `5935efe7` transcript in full showed the self-destruct trap was worse
than the [previous fix](2026-07-12-redirect-tolerance-and-embedded-cd.md)
assumed. A background worktree session ran
`cd <main> && git worktree remove --force <self>`, deleting its own cwd. The
shell fell back to the main repo, but the effective root `E` still pointed at the
now-gone worktree, so:

- every command hit the drift block,
- the offered `cd E` restore no-oped, because the target no longer existed,
- and the Bash tool deadlocked.

Two changes (`116966c`), one additive and one a reversal.

## 1. Fail open when `E` does not exist on disk

The guard's whole contract is "keep cwd at `E`". Once `E` is gone that contract
is unsatisfiable, and the only coherent behavior is to step aside.

When `E` is non-empty but `os.path.isdir(E)` is false, `PreToolUse` allows
*every* command silently, ahead of all other rules — including a leading `cd` —
so the agent can work from wherever the shell fell back to. `PostToolUse` emits a
*replacement* warning on both channels naming `E`, stating the guard is disabled
for the session, and saying to restart; it supersedes the `cd E` hint, which is
impossible once `E` is gone.

Fail-open keys on the on-disk fact, not the mechanism, so it covers *every*
deletion vector — self-removal, another session's `git worktree remove`,
`git worktree prune`, an external `rm`. `E == ""` (no `$CLAUDE_PROJECT_DIR`) is a
different degenerate state and does not trigger it.

Because `E` never comes back, the guard is effectively off for the rest of the
session. That is intended: a worktree session that removed its own worktree is
winding down at the main repo, and the `PostToolUse` notice tells the human to
restart to re-establish a valid root.

Two alternatives were rejected. A narrow `mkdir -p E && cd E` restore recreates a
hollow non-git directory, and the shell is *already* at a valid dir, so there is
nothing to restore to. Re-anchoring `E` to the fallback dir needs state the hook
does not have, and the path heuristics that would supply it were already refused
when worktree detection was designed.

## 2. `_worktree_main_root` removed

The shape-2 guard added two days earlier existed to *prevent* this trap by
blocking the self-`cd <main>`. Its advice was a dead end: `ExitWorktree` only
exits a worktree the current session created via `EnterWorktree`, so for a
background worktree session it does nothing. The guard steered the agent toward a
tool that is a no-op while blocking the one working cleanup path,
`cd <main> && git worktree remove <self>`.

Once fail-open makes the trap survivable, the guard only forbids legitimate
post-merge cleanup, so it is gone. A `$CLAUDE_PROJECT_DIR` that is itself a linked
worktree is now governed as a plain root: `cd <main> && …` is a benign
non-persisting subshell rewrite, and drift blocks with a plain `cd <E>` hint.

Keeping the guard and documenting `ExitWorktree` as the exit was rejected for the
obvious reason — the documented exit does not exist for that session shape.

The redirect tolerance and the embedded-`cd` block from the same 2026-07-12
commit are kept.

## Consciously re-accepted

An *accidental* self-destruct is no longer hard-blocked. Mitigations:
`git worktree remove` refuses a dirty worktree without `--force`; `--force` is an
explicit opt-in to destruction; the incident's real safety was the agent asking
the user first; cwd-safety was never a data-loss guard; and fail-open makes the
outcome recoverable regardless.

See "Fail open on a deleted root; drop the shape-2 self-destruct guard" in
[design.md](../design.md). Spec:
[2026-07-13-failopen-deleted-root-design.md](../../plans/2026-07-13-failopen-deleted-root-design.md).
