# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# cwd-safety

A Claude Code plugin: a dual-mode `PreToolUse(Bash)` / `PostToolUse(Bash)`
hook that keeps the agent's working directory at project root. See
`docs/design.md` for the full requirements and decisions, and
`docs/changelog.md` for how they got that way.

## Layout

- `scripts/cwd-safety.py` — the hook. Reads the hook JSON from stdin,
  dispatches on `hook_event_name`, decides against the effective root `E`
  (the enclosing git-worktree root if `cwd` is in a worktree of
  `$CLAUDE_PROJECT_DIR`, else `$CLAUDE_PROJECT_DIR`) and the payload's `cwd`
  (`W`). Pure stdlib; read-only filesystem access for worktree detection,
  otherwise no I/O beyond stdin/stdout/stderr.
- `hooks/hooks.json` — registers the script on `PreToolUse[Bash]` and
  `PostToolUse[Bash]`, both via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cwd-safety.py`.
- `tests/test_cwd_safety.py` — drives the hook as a subprocess with
  crafted JSON, asserting exit code and streams for every contract rule.
  Stdlib only (no pytest).
- `docs/design.md` — living rationale for every design decision (FR/NFR list,
  decisions (a)–(l), limitations). States what the plugin *is*, in the present
  tense. When a decision is overturned it is rewritten here in place — never
  struck through, never annotated with "formerly".
- `docs/changelog.md` — index of write-time records, newest first, one line per
  entry. Bodies live in `docs/changelog/YYYY-MM-DD-slug.md` and are **never
  revised**: a dated record is correct forever precisely because it is dated.
- `plans/` — specs and implementation plans. Prospective content only (work not
  yet done, or how something was built rather than what it is); `docs/` holds
  what is true now.
- `plugin-dev/` — vendored `claude-plugin-dev` toolkit (release recipe +
  version-guard hook). Do not edit by hand; update with
  `just update-plugin-dev vX.Y.Z`.
- `memory/` — gitlore submodule (remote `cwd-safety-gitlore-memory`) holding
  the agent's auto-memory. gitlore's commit/push hooks sync it; don't `git add`
  or commit memory content into the parent repo by hand. After a fresh
  checkout it may show uninitialized — `git submodule init memory` to register.

## Quality gate

```sh
just precommit
```

Validates the manifest and `hooks.json`, byte-compiles the hook and test,
and runs the test suite. Must be green before committing.

## The behavioral contract

Canonical statement lives in `docs/design.md` (FR1–FR9). In short, at
`PreToolUse(Bash)` with **effective root `E`** (the enclosing git-worktree root
when `cwd` is inside a worktree of `$CLAUDE_PROJECT_DIR`, detected from the
on-disk `.git` linkage, else `$CLAUDE_PROJECT_DIR` — including when
`$CLAUDE_PROJECT_DIR` is *itself* a worktree path, which is governed as a plain
root) and cwd `W`:

0. **`E` does not exist on disk** (deleted out from under the session) → **fail
   open**: allow every command silently, before all rules below. `PostToolUse`
   then says the guard is disabled — restart the session. See `docs/design.md` →
   FR7a / decision (k).

1. `W == E` and `C` is not a `cd` → allow silently.
2. `cd E` / `cd E && <rest>` (exact-path, `&&`-only) → allow from any `W`.
   Redirections may sit between the path and the `&&` (`cd E 2>&1 && <rest>`).
3. Any other leading `cd` → **block, even when `W == E`** (proactive
   drift prevention — this is the rule added when the hook was extracted).
   When a worktree is active this includes `cd $CLAUDE_PROJECT_DIR` — leave a
   worktree with the `ExitWorktree` tool, not `cd`.
   - **3a.** *Exception, only when `W == E`:* a `cd <subdir> && <cmd>` (real
     path, `&&` tail) is **rewritten** to the non-persisting `(cd <subdir> &&
     <cmd>)` via `PreToolUse` `updatedInput` and allowed, with a mandatory
     dual-channel announcement — instead of blocked. Bare `cd <subdir>`,
     `;`/`||`, a cross-root `cd $CLAUDE_PROJECT_DIR` in a worktree, and the
     same command from drift all still block. See `docs/design.md` → FR5a /
     decision (i).
   - **3b.** *Also when `W == E`:* a non-leading `cd` that runs in the current
     shell right after a top-level separator (`mkdir … && cd sub && …`,
     `echo x; cd sub`) is **blocked** (FR5b). The `cd` must immediately follow the
     separator, so `(cd sub && …)` subshells and `foo | cd sub` pipelines are not
     caught; a quoted `"&& cd"` literal is a known, block-only false positive. See
     `docs/design.md` → FR5b / decision (j).
   - **3c.** *Exception to 3b, only when `W == E`:* if that command's **first
     statement enables errexit** (`set -e` / `set -euo pipefail` / `set -o
     errexit`), the whole script is **rewritten** to the non-persisting subshell
     `(C)` — same treatment as 3a — instead of blocked (FR5c). `set -e` gives the
     same cd-first fail-fast guarantee as `&&`, so a failed `cd` aborts before the
     tail runs. Excludes `set +e`, errexit-absent `set`, and a non-first `set`.
     Unlike 3a there is **no** worktree cross-root carve-out (the subshell keeps
     cwd in the worktree). See `docs/design.md` → FR5c / decision (l).
4. `W != E`, any other command → block with the `cd E` restore hint.

At `PostToolUse(Bash)`: if `E` is gone, emit the fail-open "guard disabled —
restart" notice (replacing the impossible `cd E` hint); else if `W != E`, emit
the drift warning on both `hookSpecificOutput.additionalContext` and
`systemMessage`; else silent.

## Conventions

- **Both events run the same script.** It branches on `hook_event_name`;
  keep the two handlers (`handle_pretooluse`, `handle_posttooluse`) the
  only event-specific code.
- **Only `&&` is accepted after a root-anchored `cd`.** `;` and `||`
  break the cd-first invariant (`cmd` would run even if the `cd` failed).
  `_is_cd_to_root` enforces this — don't loosen the regex. See `docs/design.md`
  → decision (c).
- **Exact path match only.** No `normpath`/`realpath`/prefix matching —
  normalization opens substitution attacks. See `docs/design.md` → decision
  (d). The trade-off is a known sharp edge: a trailing slash or symlinked
  `$CLAUDE_PROJECT_DIR` won't match.
- **Block messages go to stderr with exit 2; the PostToolUse warning is
  non-blocking JSON on stdout.** Don't conflate the channels. Both the
  block message and the warning must be legible to the agent *and* the
  human — never soften the agent-facing text into something readable as
  an instruction to bypass.
- **The subshell rewrite is a third PreToolUse output shape:** exit 0 with
  an `allow` decision plus `hookSpecificOutput.updatedInput` on stdout. It
  is the only place the hook *mutates* a command rather than allow/block/warn.
  Two triggers share `_rewrite_to_subshell`: Rule 3a (`cd <dir> && <cmd>`) and
  Rule 3c (a `set -e` script with an embedded `cd`); each passes its own agent +
  user note. Keep the announcement (`additionalContext` + `systemMessage`)
  mandatory — a silent rewrite is a contract violation (auditability). Neither
  note echoes the command (Claude Code already surfaces the rewritten
  `updatedInput`); the 3a agent note recommends the *wrapped* follow-up form, the
  3c note names `set -e`, both user notes are terse.
  The `cd <dir> && <cmd>` matcher (`_CD_AND`/`_cd_and_target`) and the errexit
  matcher (`_starts_with_errexit`/`_SET_ERREXIT`) are deliberately looser than
  `_is_cd_to_root` — they parse one shell argument / the first statement's flags —
  but neither is *security-critical* (a wrong match only subshells or blocks), so
  neither relaxes the exact-match rule for the root anchor. See `docs/design.md` →
  FR5a / FR5c / decisions (i), (l).
- **`${CLAUDE_PLUGIN_ROOT}` in `hooks.json` is expanded by Claude Code at
  hook-fire time**, not by the shell. Keep it literal.
- **`plugin.json`'s `.version` is the last released version**; the
  `release` recipe bumps it and the vendored version-guard hook blocks
  manual edits.
- **Effective root via filesystem detection.** `main()` computes
  `effective_root = _worktree_root(cwd, project_dir) or project_dir` and threads
  it into both handlers. `_worktree_root` walks up `cwd` and reads the `.git`
  linkage to recognize a worktree of the project — there is no payload field for
  this. Read-only filesystem access; the `cd E` match stays exact. See
  `docs/design.md` → decision (h).

## Non-goals

- **Intercepting every drift vector at PreToolUse.** `pushd`, `source`d
  scripts, function calls, etc. can't all be enumerated — that's why
  PostToolUse exists as a backstop. Don't chase an exhaustive blocklist.
- **Per-repo configuration.** One guard, one `$CLAUDE_PROJECT_DIR`. No
  config surface, no allow-list of subdirectories.
- **A git-hook variant.** The guard operates on Claude Code's Bash tool
  calls, which only `PreToolUse`/`PostToolUse` expose. Git hooks fire on
  git operations and can't see the agent's cwd. See `docs/design.md` →
  decision (f).

## Releasing

```sh
just release [patch|minor|major]
```

Provided by the vendored `plugin-dev/release.just`. See
`plugin-dev/README.md`.
