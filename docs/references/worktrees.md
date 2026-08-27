# Worktrees and the unusable root

How the effective root `E` is found when the session sits inside a git
worktree, what the hook does once `E` is unusable — gone from disk, or never
set — and the decisions arguing for both. FR2, FR7a and FR7b conclude in
[design.md](../design.md);
this file is what you need while building or debugging `_worktree_root` and the
fail-open path. The `cd E` match that `E` feeds stays exact
([matchers.md](matchers.md)); the cross-root exclusion the restore rewrite
applies while a worktree is active is in
[restore-rewrite.md](restore-rewrite.md).

- One root, found on disk — **(h)** a single effective root `E`, the enclosing
  worktree detected from the on-disk `.git` linkage; a `cd` back to the main
  root while a worktree is active is blocked in favor of `ExitWorktree` ·
  **(k)** the hook fails open whenever `E` is unusable — gone from disk or never
  set — and a `$CLAUDE_PROJECT_DIR` that is itself a linked worktree is a plain
  root

---

## Mechanism

**Detection (FR2).** `_worktree_root(cwd, project_dir)` walks up from `cwd` to
the first ancestor whose `.git` is a *file* (a linked worktree's marker: the
main repo's `.git` is a directory) and reads its `gitdir:` line
(`_read_gitdir`). When that path resolves under `$CLAUDE_PROJECT_DIR/.git`
(`_is_under`, string containment only), the ancestor is the effective root;
otherwise `E` is `$CLAUDE_PROJECT_DIR`. The walk stops at
`$CLAUDE_PROJECT_DIR` itself, at a `.git` *directory* (a nested main repo is
not a worktree), and at the filesystem root, each yielding `E =
$CLAUDE_PROJECT_DIR`. `main()` computes `E` once and threads it into both
handlers along with an `in_worktree` flag, which makes the FR5 and FR6
messages name `ExitWorktree` as the way out. There is no payload field for
any of this. The walk is read-only: `os.path` stat calls and one file read, no
subprocess (NFR1, NFR3).

**The mirror shape.** When `$CLAUDE_PROJECT_DIR` is *itself* a linked worktree
(its own `.git` file reads `gitdir: <main>/.git/worktrees/<name>` — the shape
of a background worktree session), `_worktree_root` finds no *enclosing*
worktree, so `E = $CLAUDE_PROJECT_DIR` and the hook governs it as an ordinary
root: a `cd` to the main repo is a benign subdir-style FR5a rewrite, not a
cross-root block (decision (k)).

**Fail-open (FR7a).** When `E` is non-empty but `os.path.isdir(E)` is false —
a worktree removed out from under the session, an external `rm` — the guard's
contract "keep cwd at `E`" is unsatisfiable. On `PreToolUse` the hook allows
*every* command silently, before all other rules (FR3–FR6), even a leading
`cd`, so the agent can work from wherever the shell fell back to. On
`PostToolUse` it emits a *replacement* warning on both channels
(`_root_gone_message`) that names `E`, states the guard is disabled for the
session, and says to restart — superseding the FR7 `cd E` hint, which is
impossible once `E` is gone.

**No root at all (FR7b).** When `E` is empty — no `$CLAUDE_PROJECT_DIR` — the
same fail-open path runs, for a different reason: there is no root to enforce
and none to *name*. `PreToolUse` allows every command; `PostToolUse` emits
`_no_root_message`, which names `$CLAUDE_PROJECT_DIR` as the fault, says the
guard is disabled, and says outright that nothing runnable from the session
fixes it. Because neither state repairs itself, the guard is effectively off
for the remainder of the session in both.

---

## Design decisions

### (h) Worktrees: single effective root, filesystem detection

**Decision:** When `cwd` is inside a git worktree of `$CLAUDE_PROJECT_DIR`, the
hook uses that worktree root as the single effective root `E`; otherwise
`E = $CLAUDE_PROJECT_DIR`. The worktree is detected from the on-disk `.git`
linkage — walk up from `cwd` to the first ancestor whose `.git` is a file whose
`gitdir:` resolves under `$CLAUDE_PROJECT_DIR/.git`. A `cd` back to the project
root while a worktree is active is blocked (use `ExitWorktree`).

**Rationale:** A worktree changes the session `cwd` while leaving
`$CLAUDE_PROJECT_DIR` at the original repo root, so the unmodified hook blocked
every command issued from a worktree. The correct anchor there is the worktree
root. Detection cannot use a hook payload field — empirical capture of a real
PreToolUse payload from inside a managed worktree showed **no `worktree` field
exists** (a contrary claim was confabulated from web search). It cannot use the
`.claude/worktrees/<name>` path convention either, because `EnterWorktree` can
enter a worktree at an arbitrary path and the location is relocatable. The git
`.git`-file linkage is the authoritative on-disk record and ties a worktree to
a specific main repo, so it covers every location and cannot be spoofed by
merely sitting in a directory named `worktrees`. A *single* effective root
(rather than accepting both roots) suffices because `ExitWorktree` restores
`cwd`, making the clean lifecycle "work in worktree → exit → merge from main";
no single Bash call legitimately needs both roots. The cost is read-only
filesystem access (see NFR1/NFR3); the exact-match principle of decision (d) is
preserved for the `cd E` command match — only detection reads the filesystem.

This decision governs the shape where `cwd` sits *inside* a worktree of
`$CLAUDE_PROJECT_DIR`. The mirror shape — `$CLAUDE_PROJECT_DIR` set to the
worktree path *itself* (a background worktree session) — is deliberately *not*
special-cased here: `_worktree_root` finds no enclosing worktree, so `E` is that
path and it is governed as a plain root. See decision (k) for why special-casing
it is wrong.

### (k) Fail open on an unusable root; drop the shape-2 self-destruct guard

**Decision:** (1) When the effective root `E` is unusable, the hook fails open —
PreToolUse allows everything silently, PostToolUse emits a "guard disabled —
restart" notice. Two states qualify: `E` does not exist on disk (FR7a), and `E`
is empty because `$CLAUDE_PROJECT_DIR` is unset (FR7b), each with its own
notice text. (2) The `_worktree_main_root` special-case
(which treated a `$CLAUDE_PROJECT_DIR` that is itself a linked worktree as a
worktree, blocking `cd <main>` in favor of `ExitWorktree`) is removed; that
shape is now governed as a plain root.

**Rationale:** Both address the self-destruct trap: a background worktree
session runs `cd <main> && git worktree remove --force <self>` and deletes its
own cwd. The shell falls back to the main repo, but `E` still points at the
now-gone worktree, so every command hits the FR6 drift block, the `cd E` restore
no-ops (target gone), and the Bash tool deadlocks. The guard's whole contract is
"keep cwd at `E`"; once `E` is gone that contract is meaningless, and the only
coherent behavior is to step aside. Fail-open covers *every* deletion vector —
self-removal, another session's `git worktree remove`, `git worktree prune`, an
external `rm` — because it keys on the on-disk fact, not the mechanism.

The empty-`E` state reaches the same conclusion by the same argument, and adding
it costs one branch. There the contract is not merely unsatisfiable but
unstatable: every rule decides against `E`, so with `E == ""` the FR6 block
fired on *every* command and told the agent to run `cd` with no argument — a
hard deadlock behind an instruction that cannot be followed, which is strictly
worse than no guard. Failing open is the same "step aside" call. What differs
is the notice: `E` is not a path that vanished but a variable Claude Code sets
when the hook fires, so the message names the misconfiguration and says no
command in the session repairs it, rather than pointing at a root. Silence
would be wrong here — an unset `$CLAUDE_PROJECT_DIR` means the guard is not
running at all, which the human should hear about — so the fail-open shouts,
once per Bash call, on both channels.

A `_worktree_main_root` guard once tried to *prevent* this trap by blocking the
self-`cd <main>`. Its advice was a dead end: `ExitWorktree` is a no-op for a
background worktree session (it only exits a worktree the current session
created via `EnterWorktree`), so the guard steered the agent to a tool that does
nothing while blocking the one working cleanup path
(`cd <main> && git worktree remove <self>`). Once fail-open makes the trap
survivable, such a guard only forbids legitimate post-merge cleanup, so there is
none. The shape behaves as a plain root: `cd <main> && …` is a benign FR5a
restore rewrite, and drift blocks with a plain `cd <E>` hint.

**Consciously re-accepted:** an *accidental* self-destruct is no longer
hard-blocked. Mitigations: `git worktree remove` refuses a dirty worktree
without `--force`; `--force` is an explicit opt-in to destruction; the real
safety in practice is the agent asking the user before a destructive cleanup;
cwd-safety was never a data-loss guard; and fail-open makes the outcome
recoverable regardless.

---

## Rejected alternatives

For decision (h):

1. **Trust a `worktree` payload field** — rejected, it does not exist.
2. **The `.claude/worktrees/` path convention** — rejected, arbitrary and
   relocated worktrees defeat it.
3. **Accept both `$CLAUDE_PROJECT_DIR` and the worktree root** — rejected;
   `ExitWorktree` makes exit-then-merge the clean path.
4. **Have the hook set the Bash cwd** — impossible; hook output cannot redirect
   the tool's cwd.

For decision (k):

1. **Allow a narrow `mkdir -p E && cd E` restore** — rejected: it recreates a
   hollow non-git directory, and the shell is *already* at a valid dir (main),
   so there is nothing to restore to.
2. **Detect the deleted-root state and re-anchor `E` to the fallback dir** —
   rejected: needs state the hook does not have and the path heuristics
   decision (h) already refused.
3. **Keep the shape-2 guard and document `ExitWorktree` as the exit** —
   rejected: `ExitWorktree` is a no-op for this session shape, so the
   documented exit does not exist.
4. **Keep blocking when `E` is empty, with a message naming the
   misconfiguration** — rejected: a correct diagnosis does not make the block
   followable. Nothing runnable inside the session sets `$CLAUDE_PROJECT_DIR`,
   so the Bash tool stays deadlocked until a restart; the notice belongs on
   PostToolUse, where it costs nothing.
5. **Anchor `E` to `cwd` when `$CLAUDE_PROJECT_DIR` is unset** — rejected: it
   would enshrine whatever directory the shell happens to sit in as the project
   root, so drift already in progress becomes the anchor, and it is the same
   path heuristic decision (h) refused for worktree detection.
6. **Fail open on an empty `E` silently, as `PreToolUse` already does** —
   rejected: an unset `$CLAUDE_PROJECT_DIR` means the guard is not running at
   all. That is a fact about the session the human should hear, and the state
   never clears on its own.
