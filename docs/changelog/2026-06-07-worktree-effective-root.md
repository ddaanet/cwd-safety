# 2026-06-07 — Git worktrees become the effective root (v0.2.0)

A session working inside a git worktree of the project has its `cwd` in the
worktree while `$CLAUDE_PROJECT_DIR` stays at the original repo root. The
unmodified hook therefore blocked *every* command issued from a worktree: the
worktree read as permanent drift. The guard was unusable in the one workflow it
was most needed for.

The fix introduces an **effective root `E`**: the enclosing git-worktree root
when `cwd` is inside a worktree of `$CLAUDE_PROJECT_DIR`, otherwise
`$CLAUDE_PROJECT_DIR` itself. Every rule is restated against `E` rather than
against the project dir directly.

## How the worktree is detected

From the on-disk `.git` linkage: walk up from `cwd` to the first ancestor whose
`.git` is a *file* whose `gitdir:` resolves under `$CLAUDE_PROJECT_DIR/.git`.

Two other detection routes were tried and rejected:

- **A `worktree` field in the hook payload.** An empirical capture of a real
  `PreToolUse` payload from inside a managed worktree showed no such field
  exists. A contrary claim had been confabulated from web search; the design was
  pivoted to filesystem detection once the capture came back (`3a2a85e`).
- **The `.claude/worktrees/<name>` path convention.** `EnterWorktree` can enter a
  worktree at an arbitrary path and the location is relocatable, so a path
  heuristic covers only the default case and can be spoofed by merely sitting in
  a directory named `worktrees`.

The `.git` linkage is the authoritative on-disk record and ties a worktree to a
*specific* main repo, so it covers every location and cannot be faked by
directory naming. A foreign repo's worktree, or a `.git` file whose `gitdir:`
resolves elsewhere, is treated as drift rather than as a valid anchor —
including a non-UTF-8 `.git` file, which is a decode failure, not a crash
(`f8ccfe9`).

## Cost accepted

Worktree detection is the hook's first filesystem access: it stats ancestors of
`cwd` and reads one `.git` file. NFR1 (determinism) and NFR3 (zero runtime
dependencies) were relaxed to "deterministic *given filesystem state*" and
"stdlib plus read-only filesystem access"; the hook remains subprocess-free and
network-free. The exact-match principle is untouched — only *detection* reads the
filesystem; the `cd E` command match stays literal.

## One root at a time

`E` is a single root, not a pair. Accepting both `$CLAUDE_PROJECT_DIR` and the
worktree root was considered and rejected: `ExitWorktree` restores `cwd`, so the
clean lifecycle is "work in the worktree → exit → merge from main", and no single
Bash call legitimately needs both roots. Consequently a `cd` back to the project
root *while a worktree is active* is blocked like any other drift-inducing `cd`,
and the block message names `ExitWorktree` as the sanctioned exit (`08f574c`).
Block and warn messages are worktree-aware throughout, so the agent is told which
root it is anchored to.

Having the hook *set* the Bash cwd was also considered — it is not possible; hook
output cannot redirect the tool's working directory.

See "Worktrees: single effective root, filesystem detection" in
[design.md](../design.md). Spec and plan:
[2026-06-07-cwd-safety-worktree-design.md](../../plans/2026-06-07-cwd-safety-worktree-design.md),
[2026-06-07-cwd-safety-worktree.md](../../plans/2026-06-07-cwd-safety-worktree.md).
