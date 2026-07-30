# 2026-06-04 — Extracted from agent-core and renamed to `cwd-safety` (v0.1.0)

The guard started life inside `agent-core` as `hooks/submodule-safety.py` and
arrived here as a standalone plugin after four months of drift in scope. This
entry records that lineage, because the plugin's shape only makes sense against
it.

## Where it came from

**2026-01-30 — born as `hooks/submodule-safety.py`** (`a8d6355`, "Fix TDD
workflow and handoff quality issues"). Original scope: warn on git operations
that reference submodule paths, or that are issued while inside a submodule.
Wired as a `PreToolUse` warn-only hook.

**2026-01-30 — broadened to ANY non-root cwd** (`1841810`, "Fix hooks and add
production artifact vet requirement"). Submodule-specific detection dropped; the
hook warned on any Bash command issued when `cwd != project root`. From this
point the name was already a misnomer.

**2026-01-31 — dual-mode, hard block** (`0d985a2`, "Update hook config and
implement dual-mode enforcement"). Warn-at-`PreToolUse` replaced by block (exit
2); `PostToolUse` warn added as a backstop. Both driven by the same observation:
non-blocking warnings were ignored in practice — the agent kept issuing commands
from a drifted cwd and compounded the confusion. The root-anchored `cd R` escape
hatch dates from here.

**2026-01-31 — dual-channel messaging** (`618616b`, "Make hook messages visible
to both agent and user"). `PostToolUse` warnings extended to carry
`systemMessage` alongside `hookSpecificOutput.additionalContext`, so both the
agent context stream and the user's terminal see drift.

**2026-02-02 — linter cleanup** (`d243fad`). Docstring reformatted per ruff
D205, line length per E501. No behavioral change.

**2026-02-16 — `cd R && cmd` allowed** (`d2a3ecd`, "Allow cd <root> && <cmd>
pattern in submodule-safety hook"). The exact-match restore list was replaced by
the `_is_cd_to_root()` regex, with `&&` the only accepted separator — `;` and
`||` were refused because they do not make the tail conditional on the `cd`
succeeding. That security note went into the module docstring and has held
unchanged since.

**2026-05-23 — agent-core teardown** (`99920f4`, "Tear down workflow pipeline;
keep skills bundle + CLI-backing scripts"). The hooks, including
`submodule-safety.py`, were removed with the rest of the workflow pipeline; the
ecosystem was replaced by superpowers + autoMemory. The hook had been symlinked
into `/Users/david/code/home/.claude/hooks/submodule-safety.py` (since `e065fbb`,
2026-02-02) and into
`/Users/david/code/devddaanet/.claude/hooks/submodule-safety.py` (since
`b7e48cd`); both symlinks were removed on 2026-04-02 (`38166be`, `1a70e58`) when
agent-core gave way to the edify marketplace plugin.

## What the extraction changed

Three things, all landed here between `fce900a` (scaffold) and `5223263`
(v0.1.0):

1. **Renamed to `cwd-safety`.** `submodule-safety` had been wrong since
   `1841810` — the guard covers any cwd drift, and submodule descent is only its
   most common trigger. Extraction into a standalone plugin was the natural
   point to stop carrying a misleading name. Continuity was not worth keeping a
   name that misstates the scope of a security guard.

2. **Rule 3 — proactive leading-`cd` block, even from project root.** The
   agent-core hook only blocked non-root-form commands when `W != R`, so a bare
   `cd subdir` issued *from* root was allowed and drift was caught only after the
   fact by the `PostToolUse` warn. That means at least one command sequence runs
   from a drifted cwd before anything fires — the exact failure mode the guard
   exists to prevent. Since there is no legitimate need for a *persistent* cwd
   change, blocking every non-root-anchored leading `cd` unconditionally costs
   nothing and removes the whole class.

3. **Plugin packaging.** Script relocated to `scripts/cwd-safety.py`, wired via
   `hooks/hooks.json` using `${CLAUDE_PLUGIN_ROOT}` so it resolves wherever the
   plugin is installed in the user's plugin cache. The `plugin-dev` release
   toolkit was vendored in the same window (`a35af41`).

The dual-mode design and the `&&`-only invariant carried over untouched; see
"Block vs warn at PreToolUse", "Only `&&` accepted", and "Dual-mode (Pre + Post)"
in [design.md](../design.md).
