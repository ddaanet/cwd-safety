# cwd-safety — Design

Living document. Captures the rationale, requirements, and decisions behind
this plugin. Updated as the design evolves.

`cwd-safety` is a Claude Code plugin that keeps the agent's Bash working
directory at project root. It fires a single Python hook
(`scripts/cwd-safety.py`) on both `PreToolUse(Bash)` and
`PostToolUse(Bash)`. The pre-use side blocks any command that would cause or
exploit cwd drift before it runs; the post-use side warns after drift is
detected (a backstop for cases the pre-use gate cannot intercept). Together
they enforce a hard boundary: the agent executes Bash from project root or not
at all.

## Functional Requirements

**FR1.** The hook fires on `PreToolUse(Bash)` and `PostToolUse(Bash)` only.
No other tool events trigger it.

**FR2.** The hook reads `$CLAUDE_PROJECT_DIR` for the authoritative project
root `R` and `cwd` from hook stdin for the current working directory `W`.

**FR3 (allow-silent).** On `PreToolUse`: if `W == R` and the command `C` does
not begin with `cd`, the hook exits 0 silently (no output, no block).

**FR4 (root-anchored allow).** On `PreToolUse`: the command forms `cd R`,
`cd "R"`, `cd 'R'`, `cd R && <rest>`, `cd "R" && <rest>`, and
`cd 'R' && <rest>` — where `R` is an exact match for `$CLAUDE_PROJECT_DIR`,
and `&&` is the only accepted separator — are allowed from any `W`. No other
separator (`; `, `||`) qualifies.

**FR5 (proactive cd block).** On `PreToolUse`: any command whose first token
is `cd` (i.e., `cd`, `cd …`, `cd;…`, or `cd&&…`, but not `cdfoo`) that does
not satisfy FR4 is blocked with exit 2 and a stderr message — even when
`W == R`. The message names the project root and offers three sanctioned
alternatives: absolute paths, the `cd R && <command>` form, and a
non-persisting subshell `(cd subdir && <command>)`.

**FR6 (drift block).** On `PreToolUse`: if `W != R` and the command is not
the root-anchored form (FR4) and does not trigger FR5, the hook blocks with
exit 2 and a stderr message showing the current `W` and the restore command
`cd R`.

**FR7 (post-use warn).** On `PostToolUse`: if `W != R`, the hook emits a JSON
payload containing `hookSpecificOutput.additionalContext` and `systemMessage`
with a warning showing `W` and the restore command. If `W == R`, the hook
exits 0 silently.

**FR8 (wiring).** `hooks/hooks.json` registers `scripts/cwd-safety.py` via
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cwd-safety.py` for both
`PreToolUse[Bash]` and `PostToolUse[Bash]` with a 5-second timeout.

**FR9 (unknown events).** If the hook is invoked with a `hook_event_name`
other than `PreToolUse` or `PostToolUse` it exits 0 silently (forward
compatibility).

## Non-Functional Requirements

**NFR1 (determinism).** Given identical `W`, `R`, and `C`, the hook always
produces the same decision. No randomness, no external I/O, no mutable state
beyond stdin/stdout/stderr.

**NFR2 (no false positives).** A command that satisfies FR4 is never blocked,
regardless of what follows the `&&`. A command run from `W == R` that does not
start with `cd` is never blocked.

**NFR3 (zero runtime dependencies).** The hook uses Python 3 stdlib only
(`json`, `os`, `re`, `sys`). No third-party packages, no subprocess calls, no
network access.

**NFR4 (low latency).** The hook completes in well under the 5-second timeout
registered in `hooks.json`. All logic is in-process; there are no forked
subprocesses.

**NFR5 (dual-channel messaging).** Block messages go to stderr so the terminal
user sees them. PostToolUse warnings include both `hookSpecificOutput` (agent
context channel) and `systemMessage` (user-visible sidebar). Both channels
must carry the warning so neither agent nor user is blind to drift.

**NFR6 (security of the `&&` invariant).** The root-anchored form permits
`cd R && cmd` because `&&` guarantees `cmd` runs only if the `cd` succeeds.
The hook must reject `;` and `||` separators to uphold this invariant.
Exact path match only — no traversal, no normalization, no prefix matching.

**NFR7 (portability).** `hooks.json` references the script via
`${CLAUDE_PLUGIN_ROOT}` so the hook resolves correctly regardless of where
the plugin is installed in the user's plugin cache.

## Design Decisions

### (a) Block vs warn at PreToolUse

**Decision:** Block (exit 2) on PreToolUse, not merely warn.

**Rationale:** A warn-only hook was the original design (commit `a8d6355`).
Warnings were ignored in practice: the agent continued issuing commands from a
drifted cwd, compounding the confusion. Commit `0d985a2` ("implement
dual-mode enforcement") replaced warn-at-PreToolUse with block. The
requirement that drove this change is stated in the hook's module docstring:
"read-only commands from wrong cwd actively mislead the agent about context."
A misleading read (e.g., `git status` from inside a submodule) is not
harmless; it produces incorrect information that the agent treats as
authoritative. Hard block eliminates the failure mode entirely rather than
attenuating it.

**Alternatives considered:** Emit a warning and allow. Rejected because the
agent reliably ignored non-blocking warnings and proceeded to accumulate
confusion.

### (b) Proactive cd block — rule 3, even at project root

**Decision:** Block any `cd` command that is not the sanctioned root-anchored
form, including when `W == R`.

**Rationale:** The original hook (commit `0d985a2`) only blocked non-root-form
commands when `W != R`. A bare `cd subdir` issued from project root would have
been allowed, causing drift that the PostToolUse warn would then catch. The
new rule (FR5) intercepts the drift before it happens. The leading-`cd`
pattern is the most common cause of drift, so stopping it unconditionally
(whether `W == R` or not) removes the most common failure path outright. The
PostToolUse warn remains as a backstop for paths the PreToolUse gate cannot
intercept (e.g., `pushd`).

**Alternatives considered:** Allow `cd subdir` from root and rely on
PostToolUse warn. Rejected: the agent then executes at least one command
sequence from a drifted cwd before the warning fires, which is the failure
mode we are trying to eliminate. The proactive block costs nothing (there is
no legitimate need for persistent cwd change) and eliminates the whole class.

### (c) Only `&&` accepted in the root-anchored form

**Decision:** `cd R && cmd` is allowed; `cd R; cmd` and `cd R || cmd` are not.

**Rationale:** `&&` makes `cmd` conditional on the success of `cd R`. If the
`cd` fails — e.g., because `$CLAUDE_PROJECT_DIR` is wrong — `cmd` does not
run. `;` provides no such guarantee: `cmd` runs even if `cd R` fails, meaning
the hook could be spoofed by constructing a `cd <path>` that matches the regex
but points to a wrong location. `||` has the opposite semantics (run `cmd` if
`cd` fails), which makes no sense as a safety form. The choice is stated
explicitly in `scripts/cwd-safety.py`'s module docstring and in the original
commit `d2a3ecd` that introduced the `_is_cd_to_root` regex.

**Alternatives considered:** Accept `;` for ergonomics. Rejected: breaks the
security invariant; the agent can always use `&&` instead.

### (d) Exact path match, no traversal or normalization

**Decision:** The hook matches `$CLAUDE_PROJECT_DIR` literally using
`re.escape`. No `os.path.normpath`, no `os.path.realpath`, no prefix matching.

**Rationale:** Normalization opens substitution attacks (a path that normalizes
to `R` but is not literally `R`). Prefix matching allows `cd /project-root-
extra/` to pass as matching `/project-root/`. The exact match is conservative:
if `$CLAUDE_PROJECT_DIR` contains a trailing slash or uses symlinks, it will
not match a command that does not. This is a known sharp edge — the user must
use the exact string that `$CLAUDE_PROJECT_DIR` expands to. The conservative
choice is correct because the hook's job is security, not ergonomics.

**Alternatives considered:** Normalize both sides before matching. Rejected
on anti-confabulation grounds: the failure modes of normalization in edge cases
(symlinks, bind mounts, case-sensitive filesystems) are non-trivial, and there
is no evidence the exact match causes practical problems.

### (e) Dual-mode (Pre + Post) rather than a single event

**Decision:** Hook both `PreToolUse` and `PostToolUse`.

**Rationale:** `PreToolUse` alone cannot stop drift from commands it does not
know will drift. `pushd`, for example, is not detected by `_LEADING_CD`. The
PostToolUse handler is a backstop: it detects drift after the fact and warns,
leaving the agent aware that the next PreToolUse command will be blocked until
it restores cwd. The dual-mode design was introduced in commit `0d985a2`
("implement dual-mode enforcement") as a deliberate choice to handle the case
where PreToolUse blocks cannot be complete. The subsequent commit `618616b`
("Make hook messages visible to both agent and user") ensured the PostToolUse
warning reached both channels.

**Alternatives considered:** PreToolUse only, with an exhaustive list of
drift-inducing commands to block. Rejected: the list cannot be complete
(subshells, source, exec, function calls), so PostToolUse is the correct
safety net.

### (f) Native CC hook vs a git-hook materializer

**Decision:** The hook is a native Claude Code `PreToolUse`/`PostToolUse` hook,
not a git hook materialized by a `SessionStart` script (the pattern used by
`gitmoji`).

**Rationale:** cwd-safety's guard operates on Claude Code's own Bash tool
invocations, not on git operations. The `PreToolUse` event fires before every
Bash tool call and carries `cwd` in its payload; there is no equivalent in the
git hook namespace. A git-hook materializer would not have access to the
agent's working directory at command time.

**Alternatives considered:** Materialize a `pre-commit` or `prepare-commit-
msg` hook. Rejected: those events fire during git operations, not on arbitrary
Bash tool calls, so they do not cover the failure mode.

### (g) Rename submodule-safety → cwd-safety

**Decision:** The standalone plugin is named `cwd-safety`, not
`submodule-safety`.

**Rationale:** The original name (`submodule-safety.py`) reflected the
specific trigger that motivated the hook's creation: agents drifting into git
submodule directories. The hook was quickly generalized (commit `1841810`) to
cover any cwd drift, not only submodule descent. The name `submodule-safety`
was already a
misnomer in the agent-core era. Extracting the hook into a standalone plugin
was the natural point to rename it to match what it actually does.

**Alternatives considered:** Keep the name for continuity. Rejected: the name
actively misleads about the scope of the guard.

## Limitations

- **`pushd`/`popd` are not intercepted.** The `_LEADING_CD` regex only matches
  commands whose first token is `cd`. `pushd subdir` will cause drift that
  PostToolUse will warn about but PreToolUse will not block.

- **Contrived `cd R && cd subdir && cmd` passes the PreToolUse gate.** The
  `_is_cd_to_root` regex matches the entire command against the root-anchored
  pattern. A command starting with `cd R &&` satisfies FR4, so the rest of the
  pipeline executes — including a second `cd subdir`. PostToolUse is the
  backstop for this case.

- **Single-root only.** The hook is keyed to a single `$CLAUDE_PROJECT_DIR`.
  Multi-root setups (nested worktrees, monorepo sub-roots) are not supported.
  Only one root is enforced; work in a sibling worktree will be blocked if its
  path differs from `$CLAUDE_PROJECT_DIR`.

- **No quote normalization in `_LEADING_CD`.** The leading-cd regex
  `^cd(?:\s|;|&|$)` detects the unquoted `cd` builtin. A quoted `'cd'` or
  `\cd` invocation is not matched, though those forms are unusual in practice.

## History

**2026-01-30 — born as `hooks/submodule-safety.py` in agent-core** (commit
`a8d6355`, "Fix TDD workflow and handoff quality issues"). Original scope:
warn on git operations that reference submodule paths, or that are issued
while inside a submodule. Wired as a PreToolUse warn-only hook.

**2026-01-30 — broadened to ANY non-root cwd** (commit `1841810`, "Fix hooks
and add production artifact vet requirement"). Submodule-specific detection
dropped; the hook now warns on any Bash command when `cwd != project root`.

**2026-01-31 — dual-mode, hard block** (commit `0d985a2`, "Update hook config
and implement dual-mode enforcement"). Warn-at-PreToolUse replaced by block
(exit 2). PostToolUse warn added as backstop. Both changes driven by
observation that non-blocking warnings were ignored. Root-anchored `cd R`
allowed as an escape hatch.

**2026-01-31 — dual-channel messaging** (commit `618616b`, "Make hook messages
visible to both agent and user"). PostToolUse warnings extended to include
`systemMessage` alongside `hookSpecificOutput.additionalContext`, so both the
agent context stream and the user's terminal sidebar see the warning.

**2026-02-02 — linter cleanup** (commit `d243fad`, "Fix linter warnings in
hook scripts"). Docstring reformatted per ruff D205; line length corrected per
E501. No behavioral change.

**2026-02-16 — `cd R && cmd` pattern allowed** (commit `d2a3ecd`, "Allow
cd <root> && <cmd> pattern in submodule-safety hook"). The exact-match restore
list replaced by `_is_cd_to_root()` regex; only `&&` accepted as separator
(not `;` or `||`). Security note added to the docstring.

**2026-05-23 — agent-core teardown** (commit `99920f4`, "Tear down workflow
pipeline; keep skills bundle + CLI-backing scripts"). The hooks (including
`submodule-safety.py`) were removed along with the rest of the workflow
pipeline. Ecosystem replaced by superpowers + autoMemory. The hook had been
symlinked into `/Users/david/code/home/.claude/hooks/submodule-safety.py`
(since commit `e065fbb`, 2026-02-02) and into
`/Users/david/code/devddaanet/.claude/hooks/submodule-safety.py` (since
commit `b7e48cd`). Both symlinks were removed in their respective repos on
2026-04-02 when agent-core was replaced by the edify marketplace plugin
(`38166be` and `1a70e58`).

**2026-06 — extracted and hardened as `cwd-safety` plugin** (this repo).
Hook renamed to match its actual scope. Rule 3 (proactive leading-cd block,
even from root) added. Script relocated to `scripts/cwd-safety.py`; wired via
`hooks/hooks.json` using `${CLAUDE_PLUGIN_ROOT}` for portability.
