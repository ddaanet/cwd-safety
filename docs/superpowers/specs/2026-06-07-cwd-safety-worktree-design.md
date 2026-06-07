# cwd-safety × Claude Code worktrees — Design

**Date:** 2026-06-07
**Status:** Approved design, pending implementation plan
**Scope:** Make the `cwd-safety` hook honor an active Claude Code managed
worktree as the working-directory anchor, instead of blocking all commands
issued from it.

## Problem

`cwd-safety` pins the agent's Bash working directory to a single root `R =
$CLAUDE_PROJECT_DIR`, comparing it byte-for-byte against `W = cwd` from the
hook payload (`scripts/cwd-safety.py:97`, `cwd == project_dir`). Anything
else is treated as drift and blocked (Rule 4) or warned (Rule 5).

Claude Code's native worktree feature (`EnterWorktree` / `ExitWorktree`)
changes the session's working directory into a worktree while **leaving
`$CLAUDE_PROJECT_DIR` pointed at the original repository root** (confirmed
by upstream issue #36360). The result: every Bash command issued from inside
a worktree has `cwd != $CLAUDE_PROJECT_DIR`, so the hook blocks the entire
session. The two features are mutually exclusive as written.

## Research findings (authoritative)

Established from the in-session tool schemas (authoritative) and corroborated
by the Claude Code docs:

1. **`$CLAUDE_PROJECT_DIR` does not change** inside a worktree; it stays at
   the original repo root.
2. **The PreToolUse/PostToolUse stdin JSON carries a `worktree` field**
   holding the absolute worktree root when a managed worktree is active, and
   absent/null otherwise. This field — not `cwd` or any env var — is the
   authoritative signal. *(Existence and exact spelling must be verified
   empirically against the installed CC version; see Verification Gate.)*
3. **The field is per-call dynamic, never session-sticky.** `EnterWorktree`
   sets it; `ExitWorktree` clears it and restores `cwd` to the original
   directory; an agent may enter A → exit → enter B within one session, and
   the field always reflects the *currently* active tree.
4. **The worktree path is not a reliable convention.** Although the default
   location is `$CLAUDE_PROJECT_DIR/.claude/worktrees/<name>/`, `EnterWorktree`
   can enter an existing worktree at an **arbitrary path** (anything in
   `git worktree list`, e.g. a manual `git worktree add ../sibling`), and the
   location is relocatable via a `WorktreeCreate` hook. A path-prefix
   heuristic would therefore produce false negatives.
5. **A hook cannot set the Bash tool's cwd.** Hook output can block, modify
   the command string (`updatedInput`), or emit context/warnings — there is
   no output field that redirects the working directory. The guard must
   remain a block/warn guard; it cannot "fix" cwd itself.

## Decision summary

- **Single effective root, not a set.** Define `E = worktree or R`: the
  active worktree root when the `worktree` field is present and non-null,
  otherwise `$CLAUDE_PROJECT_DIR`. Every existing rule is re-expressed against
  `E` in place of `R`; no rule logic changes.
- **Detection: payload field only.** Trust solely the `worktree` field. No
  path-convention fallback, no prefix matching, no normalization — preserving
  the project's exact-match security principle (DESIGN.md decision (d)). If
  the field is absent, the feature is inert and behavior is identical to today.
- **`cd` back to main is blocked while a worktree is live.** Leaving a
  worktree is done with the `ExitWorktree` tool, which restores `cwd` and
  clears the field; the subsequent merge then runs from main under normal
  rules. There is no moment that legitimately needs both roots at once, so
  `E` is a single value and `cd $CLAUDE_PROJECT_DIR` from inside a worktree
  is drift (Rule 3).
- **Messages are worktree-aware.** When a worktree is active, block/warn text
  says "active worktree root" and directs the agent to use `ExitWorktree`
  (not `cd`) to leave.

### Why single root rather than `{R, Wt}`

An earlier iteration accepted both roots, reasoning that merging the
worktree's branch needs access to main. `ExitWorktree` removes that need: the
clean lifecycle is **work in worktree → `ExitWorktree` (cwd restores to `R`,
field clears) → merge from main under `E = R`**. Because the field is
per-call, each Bash call has exactly one active tree, so `E` is single-valued.
This is tighter (one valid cwd per state), simpler (no set/union logic), and
more faithful to cwd-safety's "one hard boundary" philosophy — the boundary
simply follows the active tree.

## Behavioral contract (rules restated against `E`)

At `PreToolUse(Bash)` with effective root `E` and cwd `W`:

1. **Allow-silent.** `W == E` and `C` is not a `cd` → exit 0, no output.
2. **Root-anchored allow.** `C` is `cd E`, `cd "E"`, `cd 'E'`, or any of those
   followed by `&& <rest>` (exact path match against `E`, `&&` only) → allow
   from any `W`.
3. **Proactive cd block.** Any other leading `cd` → block (exit 2), even when
   `W == E`. While a worktree is active this includes `cd $CLAUDE_PROJECT_DIR`;
   the message directs the agent to `ExitWorktree`.
4. **Drift block.** `W != E`, any other command → block (exit 2) with a
   restore hint `cd E`. Includes drift *within* a worktree
   (`W == Wt/subdir`).

At `PostToolUse(Bash)`:

5. **Post-warn.** `W != E` → emit `hookSpecificOutput.additionalContext` +
   `systemMessage` warning (worktree-aware). `W == E` → silent.

When no worktree is active, `E == R` and rules 1–5 are byte-for-byte the
current behavior. **Zero regression** is a requirement.

## Implementation changes (`scripts/cwd-safety.py`)

1. **Compute `E` in `main()`:**

   ```python
   worktree = hook_input.get("worktree") or ""   # absent / null / "" → ""
   effective_root = worktree or project_dir
   in_worktree = bool(worktree)
   ```

   `or ""` collapses absent, JSON `null`, and empty-string to the same
   no-worktree case.

2. **Thread `effective_root` into both handlers** in place of `project_dir`,
   plus `in_worktree` for message phrasing. Handler comparison logic
   (`cwd == root`, `_is_cd_to_root`) is unchanged.

3. **`_is_cd_to_root(command, root)`** — parameter rename only; it already
   exact-matches whatever root it is given, so `cd <Wt> && …` is accepted
   when a worktree is active.

4. **Worktree-aware messages.** When `in_worktree`, the Rule 3 and Rule 4
   block messages and the Rule 5 warning say "active worktree root" and, for
   Rule 3, instruct the agent to use the `ExitWorktree` tool rather than `cd`
   to leave. When not in a worktree, the existing "project root" wording is
   used verbatim.

The change is essentially `R → (worktree or R)` threaded through, plus
message branching. No new dependencies (NFR3 holds: still stdlib-only, no
subprocess, no filesystem I/O beyond stdin/stdout/stderr).

## Verification gate (anti-confabulation)

Because detection trusts only the `worktree` field, the **first executable
step of implementation** confirms the field empirically — not from web
search or assumption:

1. In a live Claude Code session, `EnterWorktree`, run one trivial Bash
   command, and capture the real PreToolUse stdin (a temporary raw-stdin
   dump).
2. Confirm the JSON key is literally `worktree` and that it holds the
   absolute worktree root.
3. Record the CC version that provides it.

If the key differs, adjust the code to the real name. If the field does not
exist in the installed version, the feature is **inert by design**
(`E` falls back to `R`, no regression) and the dependency is documented
rather than guessed. This step gates the rest of the work.

## Testing (`tests/test_cwd_safety.py`, stdlib subprocess driver)

New cases inject a `worktree` field into the payload JSON:

- **Allow:** wt active, `W == Wt`, non-cd → exit 0 silent.
- **Allow:** wt active, `cd Wt` and `cd Wt && cmd` → exit 0.
- **Block (Rule 3):** wt active, `cd $CLAUDE_PROJECT_DIR && …` → exit 2;
  assert the message names the `ExitWorktree` tool.
- **Block (Rule 3):** wt active, `cd subdir` → exit 2.
- **Block (Rule 4):** wt active, `W == Wt/sub` (drift inside wt), non-cd →
  exit 2, hint `cd Wt`, worktree-aware wording.
- **Block (Rule 4):** wt active, `W == R` (field present, cwd at main) →
  exit 2.
- **Post-warn:** wt active, `W == Wt` → silent; `W == Wt/sub` → warn
  (worktree-aware).
- **Fallback / regression:** `worktree` absent → all existing tests pass
  unchanged; `worktree` null and `worktree` empty-string → both treated as
  absent (`E == R`).

## Documentation updates

- **DESIGN.md:** amend FR2 (also reads `worktree`); add an FR defining the
  effective root `E`; restate FR4–FR7 against `E`; add **Decision (h)**
  capturing the single-effective-root choice, payload-field-only detection,
  the `cd`-to-main block, and their rationale (ExitWorktree enables
  exit-then-merge; `EnterWorktree path:` defeats the path convention;
  anti-confabulation). Update Limitations and append a History entry.
- **CLAUDE.md:** update the behavioral-contract summary to introduce the
  effective root and worktree handling.

## Non-goals

- **Path-convention detection.** Rejected: `EnterWorktree path:` and
  `WorktreeCreate` relocation make `.claude/worktrees/<name>` unreliable.
- **Accepting both `R` and `Wt` simultaneously.** Rejected in favor of the
  single effective root; `ExitWorktree` covers the cross-tree merge case.
- **Having the hook change cwd.** Not possible via hook output; out of scope.
- **Multi-root / nested-worktree sets.** One active tree per call, per the
  per-call `worktree` field.
