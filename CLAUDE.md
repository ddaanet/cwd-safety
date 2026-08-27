# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# cwd-safety

A Claude Code plugin: a dual-mode `PreToolUse(Bash)` / `PostToolUse(Bash)`
hook that keeps the agent's working directory at project root.

**The behavioral contract is `docs/design.md`, FR1–FR9**, with decisions
(a)–(l) and the limitations list. It is not restated here. Refer to rules by
their FR identifier (`FR5a`, `FR7a`) everywhere — code comments, tests,
changelog entries, briefs — never by any other numbering.

## Layout

- `scripts/cwd-safety.py` — the hook. Reads the hook JSON from stdin,
  dispatches on `hook_event_name`, decides against the effective root `E`
  (FR2) and the payload's `cwd` (`W`). Pure stdlib; read-only filesystem
  access for worktree detection, otherwise no I/O beyond stdin/stdout/stderr.
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
  version-guard hook).
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

## Conventions

- **Both events run the same script.** It branches on `hook_event_name`;
  keep the two handlers (`handle_pretooluse`, `handle_posttooluse`) the
  only event-specific code.
- **Never loosen `_is_cd_to_root`.** It is the security-critical matcher:
  `&&`-only after the root-anchored `cd` (decision (c)), exact path match with
  no normalization (decision (d)). The looser matchers (`_CD_AND`,
  `_SET_ERREXIT`, `_EMBEDDED_CD`) are not security-critical — a wrong match
  only subshells or blocks — and must never feed the root anchor.
- **Output channels.** Block = stderr + exit 2. PostToolUse warning =
  non-blocking JSON on stdout. Rewrite (FR5a/FR5c) = exit 0 with an `allow`
  decision plus `hookSpecificOutput.updatedInput`, both triggers through
  `_rewrite_with_restore`; the dual-channel announcement is mandatory
  (decision (i)). Never soften agent-facing text into something readable as
  an instruction to bypass.
- **Never wrap a command in `( … )`, and never recommend that form in a
  message.** A subshell hides the command from the sandbox `excludedCommands`
  matcher and mangles a trailing heredoc; the rewrite appends a newline and
  `cd <E>` instead (decision (i)). `set -e` is inert under the Bash tool
  (decision (l)) — no rule may rest on errexit.
- **Effective root is computed once in `main()`**
  (`_worktree_root(cwd, project_dir) or project_dir`) and threaded into both
  handlers. There is no payload field for it (decision (h)).
- **`${CLAUDE_PLUGIN_ROOT}` in `hooks.json` is expanded by Claude Code at
  hook-fire time**, not by the shell. Keep it literal.
- **`plugin.json`'s `.version` is the last released version**; the
  `release` recipe bumps it and the vendored version-guard hook blocks
  manual edits.

## Non-goals

- **Intercepting every drift vector at PreToolUse.** PostToolUse is the
  backstop (decision (e)). Don't chase an exhaustive blocklist.
- **Per-repo configuration.** One guard, one `$CLAUDE_PROJECT_DIR`. No
  config surface, no allow-list of subdirectories.
- **A git-hook variant.** Git hooks can't see the agent's cwd (decision (f)).

## Releasing

```sh
just release [patch|minor|major]
```

Provided by the vendored `plugin-dev/release.just`. See
`plugin-dev/README.md`.

@memory/ddaanet/shared-claude.md
