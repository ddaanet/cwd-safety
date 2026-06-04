# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# cwd-safety

A Claude Code plugin: a dual-mode `PreToolUse(Bash)` / `PostToolUse(Bash)`
hook that keeps the agent's working directory at project root. See
`DESIGN.md` for the full requirements, decisions, and history.

## Layout

- `scripts/cwd-safety.py` — the hook. Reads the hook JSON from stdin,
  dispatches on `hook_event_name`, decides against `$CLAUDE_PROJECT_DIR`
  (root `R`) and the payload's `cwd` (`W`). Pure stdlib, no I/O beyond
  stdin/stdout/stderr.
- `hooks/hooks.json` — registers the script on `PreToolUse[Bash]` and
  `PostToolUse[Bash]`, both via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cwd-safety.py`.
- `tests/test_cwd_safety.py` — drives the hook as a subprocess with
  crafted JSON, asserting exit code and streams for every contract rule.
  Stdlib only (no pytest).
- `plugin-dev/` — vendored `claude-plugin-dev` toolkit (release recipe +
  version-guard hook). Do not edit by hand; update with
  `just update-plugin-dev vX.Y.Z`.

## Quality gate

```sh
just precommit
```

Validates the manifest and `hooks.json`, byte-compiles the hook and test,
and runs the test suite. Must be green before committing.

## The behavioral contract

Canonical statement lives in `DESIGN.md` (FR1–FR9). In short, at
`PreToolUse(Bash)` with root `R` and cwd `W`:

1. `W == R` and `C` is not a `cd` → allow silently.
2. `cd R` / `cd R && <rest>` (exact-path, `&&`-only) → allow from any `W`.
3. Any other leading `cd` → **block, even when `W == R`** (proactive
   drift prevention — this is the rule added when the hook was extracted).
4. `W != R`, any other command → block with the `cd R` restore hint.

At `PostToolUse(Bash)`: if `W != R`, emit a warning on both
`hookSpecificOutput.additionalContext` and `systemMessage`; else silent.

## Conventions

- **Both events run the same script.** It branches on `hook_event_name`;
  keep the two handlers (`handle_pretooluse`, `handle_posttooluse`) the
  only event-specific code.
- **Only `&&` is accepted after a root-anchored `cd`.** `;` and `||`
  break the cd-first invariant (`cmd` would run even if the `cd` failed).
  `_is_cd_to_root` enforces this — don't loosen the regex. See `DESIGN.md`
  → decision (c).
- **Exact path match only.** No `normpath`/`realpath`/prefix matching —
  normalization opens substitution attacks. See `DESIGN.md` → decision
  (d). The trade-off is a known sharp edge: a trailing slash or symlinked
  `$CLAUDE_PROJECT_DIR` won't match.
- **Block messages go to stderr with exit 2; the PostToolUse warning is
  non-blocking JSON on stdout.** Don't conflate the channels. Both the
  block message and the warning must be legible to the agent *and* the
  human — never soften the agent-facing text into something readable as
  an instruction to bypass.
- **`${CLAUDE_PLUGIN_ROOT}` in `hooks.json` is expanded by Claude Code at
  hook-fire time**, not by the shell. Keep it literal.
- **`plugin.json`'s `.version` is the last released version**; the
  `release` recipe bumps it and the vendored version-guard hook blocks
  manual edits.

## Non-goals

- **Intercepting every drift vector at PreToolUse.** `pushd`, `source`d
  scripts, function calls, etc. can't all be enumerated — that's why
  PostToolUse exists as a backstop. Don't chase an exhaustive blocklist.
- **Per-repo configuration.** One guard, one `$CLAUDE_PROJECT_DIR`. No
  config surface, no allow-list of subdirectories.
- **A git-hook variant.** The guard operates on Claude Code's Bash tool
  calls, which only `PreToolUse`/`PostToolUse` expose. Git hooks fire on
  git operations and can't see the agent's cwd. See `DESIGN.md` →
  decision (f).

## Releasing

```sh
just release [patch|minor|major]
```

Provided by the vendored `plugin-dev/release.just`. See
`plugin-dev/README.md`.
