# cwd-safety × Claude Code worktrees — Design

**Date:** 2026-06-07
**Status:** Approved design, in implementation
**Scope:** Make the `cwd-safety` hook honor an active git worktree as the
working-directory anchor, instead of blocking all commands issued from it.

## Problem

`cwd-safety` pins the agent's Bash working directory to a single root `R =
$CLAUDE_PROJECT_DIR`, comparing it byte-for-byte against `W = cwd` from the
hook payload (`scripts/cwd-safety.py`, `cwd == project_dir`). Anything else is
treated as drift and blocked (Rule 4) or warned (Rule 5).

Claude Code's native worktree feature (`EnterWorktree` / `ExitWorktree`, and
subagent `isolation: "worktree"`) changes the session's working directory into
a worktree while **leaving `$CLAUDE_PROJECT_DIR` pointed at the original
repository root**. The result: every Bash command issued from inside a
worktree has `cwd != $CLAUDE_PROJECT_DIR`, so the hook blocks the entire
session. The two features are mutually exclusive as written. (Reproduced
during this work: a subagent in an `isolation: "worktree"` tree had its first
Bash call blocked by cwd-safety's Rule 4.)

## Research findings (empirical)

Established from the in-session tool schemas and, decisively, from capturing a
**real PreToolUse payload from inside a managed worktree** (a throwaway
`isolation: "worktree"` subagent + a temporary stdin-capture hook):

1. **`$CLAUDE_PROJECT_DIR` does not change** inside a worktree; it stays at the
   original repo root.
2. **There is NO `worktree` field in the hook payload.** A claim that one
   exists came from web-search and was confabulated. The actual PreToolUse
   payload fields are: `session_id`, `transcript_path`, `cwd`,
   `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`,
   `tool_use_id`, plus `effort` (top-level) or `agent_id` + `agent_type`
   (subagents). The only directory signal is `cwd`.
3. **Managed worktrees live under
   `$CLAUDE_PROJECT_DIR/.claude/worktrees/<name>/` by default**, but
   `EnterWorktree` can enter an existing worktree at an **arbitrary path**
   (anything in `git worktree list`, e.g. `git worktree add ../sibling`), and
   the location is relocatable via a `WorktreeCreate` hook. So a path-prefix
   heuristic on `.claude/worktrees/` would miss real worktrees.
4. **A hook cannot set the Bash tool's cwd.** Hook output can block, modify the
   command string (`updatedInput`), or emit context — there is no output field
   that redirects the working directory. The guard must remain block/warn; it
   cannot "fix" cwd itself.
5. **Worktree state is per-Bash-call.** Each call's `cwd` reflects whatever
   tree is currently active; `ExitWorktree` restores `cwd` to the original
   directory. There is no session-sticky state to track.

Because there is no field and no reliable path convention, **detection must be
derived from the filesystem**: is `cwd` inside a git worktree of `R`?

## Decision summary

- **Single effective root.** Define `E = (worktree root containing cwd, if cwd
  is a worktree of R) else R`. Every existing rule is re-expressed against `E`
  in place of `R`; no rule logic changes.
- **Detection: filesystem git-worktree check.** Walk up from `cwd`; the
  worktree root is the first ancestor whose `.git` is a *file* containing
  `gitdir: <path>` where `<path>` resolves under `R/.git/worktrees/`. Stop at
  `R` (the main working tree is not a linked worktree) and at the filesystem
  root. Reads only — no subprocess. Returns the worktree root, or nothing (so
  `E` falls back to `R`).
- **`cd` back to main is blocked while a worktree is active.** Leaving a
  worktree is done with the `ExitWorktree` tool, which restores `cwd`; the
  subsequent merge then runs from main under `E = R`. No single Bash call
  legitimately needs both roots at once, so `E` is single-valued and
  `cd $CLAUDE_PROJECT_DIR` from inside a worktree is drift (Rule 3).
- **Messages are worktree-aware.** When a worktree is active, block/warn text
  says "active worktree root" and directs the agent to use `ExitWorktree` (not
  `cd`) to leave.

### Why a filesystem check rather than the payload field or path convention

The payload field does not exist (finding 2). The `.claude/worktrees/` path
convention is unreliable for arbitrary-path and relocated worktrees (finding
3). The git `.git`-file linkage is the authoritative on-disk record of a
worktree and ties it to a specific main repo, so it covers every worktree
location and cannot be spoofed by merely sitting in a directory named
`worktrees`. The cost is filesystem I/O (see NFR revision below); the user
accepted that trade for robustness.

### Why single root rather than `{R, Wt}`

An earlier iteration accepted both roots, reasoning that merging the worktree's
branch needs access to main. `ExitWorktree` removes that need: the clean
lifecycle is **work in worktree → `ExitWorktree` (cwd restores to `R`) → merge
from main under `E = R`**. Because state is per-call, each Bash call has
exactly one active tree, so `E` is single-valued. This is tighter (one valid
cwd per state), simpler (no set/union logic), and faithful to cwd-safety's "one
hard boundary" philosophy — the boundary simply follows the active tree.

## Behavioral contract (rules restated against `E`)

At `PreToolUse(Bash)` with effective root `E` and cwd `W`:

1. **Allow-silent.** `W == E` and `C` is not a `cd` → exit 0, no output.
2. **Root-anchored allow.** `C` is `cd E`, `cd "E"`, `cd 'E'`, or any of those
   followed by `&& <rest>` (exact path match against `E`, `&&` only) → allow
   from any `W`.
3. **Proactive cd block.** Any other leading `cd` → block (exit 2), even when
   `W == E`. While a worktree is active this includes `cd $CLAUDE_PROJECT_DIR`;
   the message directs the agent to `ExitWorktree`.
4. **Drift block.** `W != E`, any other command → block (exit 2) with a restore
   hint `cd E`. Includes drift *within* a worktree (`W == Wt/subdir`).

At `PostToolUse(Bash)`:

5. **Post-warn.** `W != E` → emit `hookSpecificOutput.additionalContext` +
   `systemMessage` warning (worktree-aware). `W == E` → silent.

When `cwd` is not inside a worktree of `R`, `E == R` and rules 1–5 are
byte-for-byte the current behavior. **Zero regression** is a requirement.

## Implementation (`scripts/cwd-safety.py`)

1. **Detection helpers (new), filesystem-based, returning "" for "not a
   worktree" so they compose with `or`:**

   - `_worktree_root(cwd, project_dir) -> str` — walk up from `cwd`; return the
     first ancestor directory whose `.git` is a file whose `gitdir:` resolves
     under `project_dir/.git`; "" if none, if `project_dir` is reached first,
     or if a `.git` *directory* (a nested main repo) is hit first.
   - `_read_gitdir(dotgit_file) -> str` — read a worktree `.git` file, return
     the absolute `gitdir:` path (resolving relative paths against the file's
     dir); "" on any failure.
   - `_is_under(path, parent) -> bool` — string containment (`path == parent`
     or `path` starts with `parent + os.sep`); no symlink/realpath resolution.

2. **Compute `E` in `main()`:**

   ```python
   worktree = _worktree_root(cwd, project_dir)
   effective_root = worktree or project_dir
   in_worktree = bool(worktree)
   ```

3. **Thread `effective_root` + `in_worktree` into both handlers** in place of
   `project_dir`. Handler comparison logic (`cwd == root`, `_is_cd_to_root`) is
   unchanged. `_is_cd_to_root(command, root)` already exact-matches whatever
   root it is given, so `cd <Wt> && …` is accepted when a worktree is active.

4. **Worktree-aware messages.** When `in_worktree`, the Rule 3/Rule 4 block
   messages and the Rule 5 warning say "active worktree root" and, for Rule 3,
   instruct the agent to use the `ExitWorktree` tool rather than `cd` to leave.
   When not in a worktree, the existing "project root" wording is used verbatim
   (no regression in wording).

## Verification result (anti-confabulation gate — completed)

The gate was run before relying on any detection signal. Method: a temporary
`PreToolUse(Bash)` capture hook in `.claude/settings.local.json` + an
`isolation: "worktree"` subagent running one Bash command; the payload was read
with the file Read tool (not Bash, to avoid the capture overwriting itself).

Result: **no `worktree` field exists** in this Claude Code version. The design
pivoted from "trust the field" to the filesystem check above. All probe
artifacts (capture hook, temp worktree, temp files) were removed.

## Non-functional requirement revisions

- **NFR1 (determinism) / NFR3 (no I/O):** previously "no I/O beyond
  stdin/stdout/stderr, no subprocess." This must be relaxed: detection now does
  **filesystem reads** (`os.path.isfile/isdir`, reading one `.git` file). It
  remains **subprocess-free and network-free**, and deterministic given
  filesystem state. DESIGN.md's NFRs are updated to permit read-only filesystem
  access for worktree detection.
- **NFR4 (low latency):** the walk is a handful of `stat`s and one small file
  read — far under the 5s timeout. Unaffected.
- **Security:** the gitdir-under-`R/.git` check confirms the worktree belongs
  to *this* repo; it cannot be satisfied by merely being in a directory named
  `worktrees`. The security-sensitive `cd E` command match stays exact (no
  normalization) per decision (d); only the *detection* of `E` reads the
  filesystem.

## Testing (`tests/test_cwd_safety.py`, stdlib subprocess driver)

Because detection is filesystem-based, worktree cases need **real fixtures**.
At startup the suite builds a temp tree (cleaned up via `atexit`):

- `PROJ/` — a project root (with `.git/worktrees/wt1/` present for realism).
- `WT/` — a worktree root **outside** `PROJ` (proving location-independence),
  whose `.git` *file* contains `gitdir: PROJ/.git/worktrees/wt1`; plus `WT/src`.
- `OTHER/` — a foreign repo with a `.git` *directory* (must NOT be seen as a
  worktree of `PROJ`).
- `EVIL/` — a `.git` *file* whose `gitdir:` points outside `PROJ/.git` (must
  NOT be accepted — spoof guard).

`run()` gains a `root=ROOT` parameter (sets `CLAUDE_PROJECT_DIR`); the
`worktree=` payload-field parameter is removed. Cases:

- **Allow:** `W == WT`, non-cd → exit 0; `cd WT`, `cd WT && ls` → exit 0.
- **Block (Rule 3):** `W == WT`, `cd $PROJ && …` → exit 2, message names
  `ExitWorktree`; `cd subdir` → exit 2.
- **Block (Rule 4):** `W == WT/src` (drift inside wt) → exit 2, hint `cd WT`,
  worktree-aware; `W == PROJ/subdir` (ordinary drift, no worktree) → exit 2,
  ordinary wording.
- **Detection guards:** `W == OTHER` → exit 2 (foreign `.git` dir, `E == PROJ`,
  drift); `W == EVIL` → exit 2 (spoofed `.git` file rejected, `E == PROJ`).
- **Post-warn:** `W == WT` → silent; `W == WT/src` → warn (worktree-aware).
- **Regression:** all pre-existing fake-path tests (`ROOT`, `SUB`) still pass —
  `_worktree_root` reaches `project_dir` (or finds no `.git`) and returns "",
  so `E == R` with no filesystem dependency for those cases.

## Documentation updates

- **DESIGN.md:** amend FR2 (effective root from filesystem detection); restate
  FR3–FR7 against `E`; relax NFR1/NFR3 to permit read-only filesystem access;
  add **Decision (h)** — single effective root, filesystem git-worktree
  detection, `cd`-to-main blocked while active, and the rationale (no payload
  field; path convention unreliable; `.git` linkage authoritative;
  `ExitWorktree` enables exit-then-merge). Update Limitations and append a
  History entry recording the field finding.
- **CLAUDE.md:** update the behavioral-contract summary to introduce the
  effective root and worktree handling; add a Conventions bullet on detection.

## Non-goals

- **Payload-field detection.** No such field exists (verified).
- **Path-convention (`.claude/worktrees/`) detection.** Rejected: arbitrary-path
  and relocated worktrees defeat it.
- **Accepting both `R` and the worktree root simultaneously.** Rejected in favor
  of the single effective root; `ExitWorktree` covers the cross-tree merge case.
- **Symlink/realpath normalization.** Detection uses string containment only;
  the `cd E` match stays exact (decision (d) preserved).
- **Having the hook change cwd.** Not possible via hook output; out of scope.
