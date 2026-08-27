# cwd-safety — fail open on a deleted root; drop the shape-2 self-destruct guard — Design

**Date:** 2026-07-13
**Status:** Approved design, ready for implementation
**Scope:** Make the hook recover a session whose effective root `E` was deleted
out from under it (a "bricked" worktree session), and remove the shape-2
(`$CLAUDE_PROJECT_DIR`-is-itself-a-worktree) self-destruct guard shipped in
commit `d8b6a3a`, which rests on a false premise and blocks legitimate cleanup.

## Problem

Two linked defects, both grounded in background worktree session `5935efe7`
(transcript examined; the session wrote a `feedback_worktree_hook_deadlock`
memory about it).

### Defect 1 — a deleted root bricks the session

When `$CLAUDE_PROJECT_DIR` is itself a worktree and that worktree is removed
(e.g. `git worktree remove --force <self>`), the directory `E` points at ceases
to exist. Observed aftermath, verified from the transcript:

1. The Bash tool's shell **falls back to the main repo** — the post-deletion
   payload `cwd` became `/Users/david/code/home`, *not* the dead worktree path.
2. But `E = $CLAUDE_PROJECT_DIR` is still the **deleted** worktree path. So
   `W != E` on every command → permanent Rule 4 drift block.
3. The prescribed restore `cd E` runs, no-ops (target gone), cwd stays at the
   main repo → still `!= E` → still blocked.
4. `ExitWorktree` is a **no-op** here — it only exits worktrees created by
   `EnterWorktree` *in the current session*; a background worktree session does
   not qualify.
5. Every escape (`cd` elsewhere, `mkdir -p E && cd E`, any command) is blocked
   by the same drift rule. The Bash tool is fully deadlocked. Recovery in the
   incident was only via the user running commands out-of-band (`!<cmd>`) and
   the agent using Write/Edit (which bypass the Bash hook).

A `PreToolUse` hook cannot persistently set the Bash cwd (DESIGN decision (h)
alternative 4), so nothing can *move* the shell back. But the shell is already
in a valid directory — the guard is simply pinned to a root that no longer
exists and refuses to let go.

### Defect 2 — the shape-2 self-destruct guard has a false premise

Commit `d8b6a3a` added `_worktree_main_root`: it detects "CPD is itself a linked
worktree," sets `in_worktree=True`, and blocks `cd <main> && …` with the advice
*"leave the worktree with `ExitWorktree`."* But per finding 4 above,
`ExitWorktree` does nothing for this session shape. So the guard blocks the one
working cleanup path (`cd main && git worktree remove <self>`) and directs the
agent to a tool that no-ops — a guard whose advice is a dead end. It also makes
the drift-restore hint wrong: it says "use ExitWorktree" when the correct
restore for shape 2 is simply `cd <worktree-path>` (which works while the
worktree exists).

The guard existed *only* to prevent the trap from Defect 1. Once Defect 1 is
made recoverable, the guard prevents a now-survivable event at the cost of
blocking legitimate in-session cleanup.

## Design

Two changes. One adds a recovery backstop; the other removes a misfiring guard.

### Change A — fail open when the effective root does not exist

The guard's entire contract is "keep cwd at `E`." If `E` does not exist, that
contract is unsatisfiable and meaningless, so the only coherent behavior is to
step aside.

**PreToolUse.** At the very top of `handle_pretooluse`, before every other rule
(before the leading-`cd` block, before FR5b, before the drift block):

```python
if root and not os.path.isdir(root):
    sys.exit(0)   # effective root deleted — guard contract void; allow silently
```

Allow *silently* (no note): `PreToolUse` fires on every command, has no state to
"one-shot" a note, and the agent needs to work unimpeded. The explanation is
delivered once-per-command through PostToolUse instead (below), where a warning
channel already exists.

**PostToolUse.** When `root` is non-empty and does not exist on disk, emit a
*replacement* warning instead of the generic drift warning — because the generic
warning's advice (`cd E` to restore) is impossible when `E` is deleted:

> cwd-safety: the project root `<E>` no longer exists — the working-directory
> guard is disabled for this session. You are likely in the main repo now.
> Restart the session to re-establish a valid root.

Emitted on both `hookSpecificOutput.additionalContext` and `systemMessage`, same
dual-channel shape as the existing drift warning. This check must come before
the ordinary `W != E` warning branch.

**Property to name in the docs:** after any root deletion, cwd-safety is
effectively **off for the remainder of that session** — `E` stays dead, so every
command fail-opens. This is acceptable for a worktree session that just removed
its own worktree (its job is done; it is winding down at main), and the
PostToolUse note tells the human to restart to re-anchor.

**Cost:** one `os.path.isdir` per event. Read-only filesystem access, already
sanctioned by decision (h) / NFR1 / NFR3. Exact-string, no normalization.

### Change B — remove the shape-2 self-destruct guard

Revert the `_worktree_main_root` portion of `d8b6a3a`:

- **Delete** `_worktree_main_root`.
- **`main()`** reverts to the two-line worktree derivation:
  ```python
  worktree = _worktree_root(cwd, project_dir)
  effective_root = worktree or project_dir
  in_worktree = bool(worktree)
  ```
  and passes `project_dir` where `main_root` was threaded.
- **`handle_pretooluse`** signature's last parameter reverts from `main_root` to
  `project_dir`; the cross-root subshell exclusion reverts to
  `not (in_worktree and target == project_dir)`.

Resulting shape-2 behavior (CPD is a worktree, treated as a plain root `E=CPD`):

- Drift block advises `cd <worktree-path>` — correct while the worktree exists;
  no misleading `ExitWorktree` hint.
- `cd <subdir> && <cmd>` → FR5a subshell rewrite (unchanged).
- `cd <main> && <cmd>` → FR5a subshell rewrite (no longer a cross-root block).
  Benign main commands run in a non-persisting subshell (stays anchored);
  `cd main && git worktree remove <self>` runs the removal, deletes cwd, shell
  falls back to main, next command hits **Change A fail-open** → agent continues
  from main. The legitimate post-merge cleanup works, with one recoverable blip.

**Consciously re-accepted:** an *accidental* self-destruct is no longer
hard-blocked. Mitigations that make this acceptable: `git worktree remove`
without `--force` already refuses a dirty worktree; `--force` is an explicit
opt-in to destruction; the incident's real safety was the agent asking the user
first; cwd-safety was never a data-loss guard; and fail-open makes the outcome
recoverable regardless. Fail-open also covers the deletion vectors the guard
never could — another session removing the worktree, `git worktree prune`,
external `rm`, a `source`d script — so it is the necessary backstop and the
guard was only a narrow special-case on top of it.

### What stays unchanged

- Redirect tolerance in FR4/FR5a (`cd E 2>&1 && …`) — worktree-independent.
- The embedded-`cd` block FR5b (`mkdir … && cd sub && …`) —
  worktree-independent.
- Shape-1 worktree handling via `_worktree_root` (cwd inside a worktree of CPD)
  and its `ExitWorktree` advice — untouched; out of scope.

## Edge cases

- `root == ""` (no `$CLAUDE_PROJECT_DIR`): the `root and …` guard skips
  fail-open — a missing project dir is a different degenerate state, unchanged.
- Root exists but `cwd` is deleted while root is fine: not the bricked case;
  `W != E` drift block applies as normal (root is a valid restore target).
- Fail-open and a leading `cd` in the same command: fail-open wins (it is
  first), so even `cd <anywhere>` is allowed while root is missing — intended;
  the agent must be free to leave.
- Symlinked / trailing-slash root: `os.path.isdir` follows symlinks and
  tolerates a trailing slash, so a live-but-oddly-spelled root is *not* seen as
  deleted. (The exact-match sharp edge of decision (d) is unaffected — that
  governs the `cd E` *command* match, not existence detection.)

## Testing (drives TDD)

New/changed `tests/test_cwd_safety.py` cases (stdlib harness,
subprocess-driven):

- **Fail-open, PreToolUse:** with `$CLAUDE_PROJECT_DIR` set to a
  **non-existent** path, assert `ls`, an arbitrary `cd elsewhere`, and
  `mkdir -p x && cd x` are all **allowed** (exit 0, no block), from both
  `W == E` and `W != E` payloads.
- **Fail-open, PostToolUse:** with a non-existent root, assert the warning fires
  and its text names the root and says "disabled"/"restart" — and does **not**
  emit the generic `cd E` restore hint.
- **Root exists → no fail-open:** existing drift/allow/block behavior is
  unchanged when the root is a real directory (regression guard).
- **Remove shape-2 assertions:** delete the `cpd-wt:` block that asserted the
  guard (`cd MAIN && git worktree remove` BLOCKED, `ExitWorktree` in message,
  drift message says "worktree"). Replace with shape-2-as-plain-root
  expectations: `cd <main> && <cmd>` from CPD-is-worktree is **rewritten to a
  subshell** (not blocked); drift from a CPD-worktree subdir blocks with a
  `cd <CPD>` restore hint and **no** `ExitWorktree` mention.
- **Retain** the redirect-tolerance and FR5b cases from `d8b6a3a` verbatim.

`just precommit` must be green.

## Docs to update during implementation

- **DESIGN.md:** add an FR for fail-open (PreToolUse allow + PostToolUse
  replacement warning); add a decision (k) for "fail open on a deleted root +
  removal of the shape-2 guard," recording the false-`ExitWorktree`-premise
  rationale and the consciously re-accepted self-destruct; **revert** the
  decision (h) shape-2 extension paragraph and the FR2 shape-2 sentence added by
  `d8b6a3a`; add a Limitations bullet ("guard disables itself for the rest of a
  session after root deletion"); add a history entry.
- **CLAUDE.md:** revert the contract-summary additions about the CPD-is-worktree
  shape and `ExitWorktree`-for-shape-2; add a one-line note on the fail-open
  behavior.

## Out of scope (non-goals, unchanged)

- Persisting a cwd change from a hook — impossible (decision (h) alt 4).
- Re-anchoring `E` to the fallback directory after deletion — would need state
  the hook does not have and path heuristics decision (h) rejected; fail-open
  (disable) is the honest behavior instead.
- Shape-1 `ExitWorktree` correctness — separate concern, not touched here.
