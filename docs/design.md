# cwd-safety — Design

Living design document. States what the plugin *is* and why it has this shape.
Present tense throughout: when a decision is overturned it is rewritten here in
place, and the reversal gets a dated entry in [changelog.md](changelog.md).

This document is a hub. `docs/references/` holds one node per mechanism —
[matchers.md](references/matchers.md), [worktrees.md](references/worktrees.md),
[restore-rewrite.md](references/restore-rewrite.md) — each carrying the detail
behind a requirement here and the decisions and rejected alternatives arguing
for it. The sections here summarize and conclude; **read the node before making
a claim about, or changing, the mechanism it argues**. Each requirement and
decision below names its node. The hub and every node stay under 400
hard-wrapped lines (`just check-docs`), so a reader loads any one of them whole.

`cwd-safety` is a Claude Code plugin that keeps the agent's Bash working
directory at project root. It fires a single Python hook
(`scripts/cwd-safety.py`) on both `PreToolUse(Bash)` and
`PostToolUse(Bash)`. The pre-use side blocks any command that would cause or
exploit cwd drift before it runs — or, for two ergonomic cases issued from
root (a `cd <subdir> && <cmd>`, and a `set -e` script with an embedded `cd`),
appends a `cd` back to root rather than blocking; the post-use side warns after
drift is detected (a backstop for cases the pre-use gate cannot intercept).
Together they enforce a hard boundary: the agent executes Bash from project
root or not at all.

## Functional Requirements

**FR1.** The hook fires on `PreToolUse(Bash)` and `PostToolUse(Bash)` only.
No other tool events trigger it.

**FR2.** The hook reads `$CLAUDE_PROJECT_DIR` for the project root and `cwd`
(`W`) from hook stdin. The **effective root** `E` is the enclosing git-worktree
root when `W` is inside a worktree of `$CLAUDE_PROJECT_DIR` — detected from the
on-disk `.git` linkage (a worktree's `.git` is a file whose `gitdir:` resolves
under `$CLAUDE_PROJECT_DIR/.git`) — otherwise `$CLAUDE_PROJECT_DIR`. All
decisions below are made against `E`. A `$CLAUDE_PROJECT_DIR` that is *itself*
a linked worktree is governed as an ordinary root (decision (k)). Detection in
[worktrees.md](references/worktrees.md).

**FR3 (allow-silent).** On `PreToolUse`: if `W == E` and the command `C` does
not begin with `cd`, the hook exits 0 silently (no output, no block).

**FR4 (root-anchored allow).** On `PreToolUse`: the command forms `cd E`,
`cd "E"`, `cd 'E'`, `cd E && <rest>`, `cd "E" && <rest>`, and `cd 'E' && <rest>`
— where `E` is an exact match for the effective root, and `&&` is the only
accepted separator — are allowed from any `W`. No other separator (`;`, `||`)
qualifies. Redirections may sit between the target and the `&&`
(`cd E 2>&1 && <rest>`); they neither block the allow nor license another
separator (decision (j)). Grammar in [matchers.md](references/matchers.md).

**FR5 (proactive cd block).** On `PreToolUse`: any command whose first token is
`cd` (i.e., `cd`, `cd …`, `cd;…`, or `cd&&…`, but not `cdfoo`) that does not
satisfy FR4 is blocked with exit 2 and a stderr message — even when `W == E`.
The message names the effective root and offers three sanctioned alternatives:
absolute paths or `git -C <dir>`, the `cd E && <command>` form, and the leading
`cd <subdir> && <command>` form (which FR5a rewrites). It never advertises a
`( … )` subshell (decision (i)). When a worktree is active, this includes a
`cd` back to `$CLAUDE_PROJECT_DIR`: leaving a worktree is done with the
`ExitWorktree` tool, not a raw `cd`, and the message says so.

**FR5a (restore rewrite).** On `PreToolUse`, when `W == E`: a command of the
form `cd <dir> && <rest>` — a leading `cd` to a single directory argument
(quoted or backslash-escaped spaces allowed, redirections allowed before the
`&&`) joined by `&&` to a non-empty tail, not satisfying FR4 — is **not**
blocked. The hook returns `permissionDecision: "allow"` with
`hookSpecificOutput.updatedInput` replacing the command with `C`, a blank line,
and `cd <E>` (shell-quoted), so cwd is restored by the trailing statement —
never a `( … )` subshell, never `;`. The rewrite is announced on both channels,
never silent, and neither note echoes the command. Excluded, falling through to
the FR5 block: a bare `cd <dir>`; a pathless `cd && …`; `cd a b && …`; the `;`
and `||` separators; and, while a worktree is active, a `cd` to
`$CLAUDE_PROJECT_DIR` (compared de-quoted). From a drifted cwd (`W != E`) the
same command is blocked (FR6). Mechanism and announcement text in
[restore-rewrite.md](references/restore-rewrite.md).

**FR5b (embedded-cd block).** On `PreToolUse`, when `W == E`: a command that is
not itself a leading `cd` but contains a `cd` running in the current shell right
after a top-level sequencing operator (`&&`, `||`, `;`, `&`, or a newline) or as
the first statement of a compound body (`then cd`, `do cd`, `{ cd`) — e.g.
`mkdir -p tools && cd tools && …` — is blocked with the FR5 message. The command
is masked before matching: quoted strings, `#` comments, heredoc bodies and
every parenthesised region (`( … )`, `$( … )`, `<( … )`) are blanked, so a `cd`
inside any of them — a literal that mentions `&& cd`, a `python3 - <<'EOF'`
script, `(set -e; cd x; …)` — is never caught, and neither is a `foo | cd sub`
pipeline. This is a drift detector with a scanner in front of it, not a shell
parser; what it still misses only drifts into PostToolUse (see Limitations).
The contrived leading form `cd E && cd sub && …` satisfies FR4 and is allowed.
Decision (j), grammar in [matchers.md](references/matchers.md).

**FR5c (`set -e` restore rewrite).** On `PreToolUse`, when `W == E`: a command
whose first effective statement (after leading blank or `#`-comment lines) is a
`set` builtin that enables errexit (`set -e`, `set -eu`, `set -euo pipefail`,
`set -ex`, `set -o errexit`) and which FR5b would otherwise block is **not**
blocked; it gets the same allow-with-`updatedInput` rewrite as FR5a — `C`, a
blank line, `cd <E>` — and a dedicated dual-channel announcement that also
states that `set -e` does not abort a Bash tool command. `set -e` is the
trigger, not the guarantee: errexit is inert under the Bash tool (decision (l)),
so the appended restore is what keeps cwd at `E`. Excluded, falling through to
the FR5b block: the `+` forms and errexit-absent `set`; a `set` that is not the
first statement; `setup && …`. A `set -e` script with no embedded `cd` stays
allow-silent (FR3). No cross-root carve-out is mirrored from FR5a. From a
drifted cwd the script is blocked (FR6). Mechanism in
[restore-rewrite.md](references/restore-rewrite.md).

**FR6 (drift block).** On `PreToolUse`: if `W != E` and the command is not
the root-anchored form (FR4) and does not trigger FR5, the hook blocks with
exit 2 and a stderr message showing the current `W` and the restore command
`cd E`.

**FR7 (post-use warn).** On `PostToolUse`: if `W != E`, the hook emits a JSON
payload containing `hookSpecificOutput.additionalContext` and `systemMessage`
with a warning showing `W` and the restore command. If `W == E`, the hook
exits 0 silently.

**FR7a (fail-open on a deleted root).** When `E` is non-empty but no longer
exists on disk, the guard's contract "keep cwd at `E`" is unsatisfiable. On
`PreToolUse` the hook then allows *every* command silently, before all other
rules (FR3–FR6). On `PostToolUse` it emits a *replacement* warning (both
channels) that names `E`, states the guard is disabled for the session, and
says to restart. Decision (k), mechanism in
[worktrees.md](references/worktrees.md).

**FR7b (fail-open with no root at all).** When `E` is empty — no
`$CLAUDE_PROJECT_DIR` — there is no root to enforce and none to name: the FR6
block's restore hint would read `cd` with no argument. `PreToolUse` therefore
allows every command, like FR7a, and `PostToolUse` shouts a *replacement*
notice (both channels) that names `$CLAUDE_PROJECT_DIR` as the fault, states
the guard is disabled for the session, and says no command in the session can
repair it. The hook never blocks and never emits an empty `cd`. Decision (k),
mechanism in [worktrees.md](references/worktrees.md).

**FR8 (wiring).** `hooks/hooks.json` registers `scripts/cwd-safety.py` via
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cwd-safety.py"` — quoted, so an install
path containing a space does not word-split — for both
`PreToolUse[Bash]` and `PostToolUse[Bash]` with a 5-second timeout.

**FR9 (unknown events).** If the hook is invoked with a `hook_event_name`
other than `PreToolUse` or `PostToolUse` it exits 0 silently (forward
compatibility).

## Non-Functional Requirements

**NFR1 (determinism).** Given identical `W`, `E`, and `C`, the hook always
produces the same decision. No randomness, no external I/O, no mutable state
beyond stdin/stdout/stderr. Worktree detection reads the filesystem (`os.path`
stat calls and one `.git` file); the hook is deterministic *given filesystem
state*. It performs no network access and spawns no subprocess.

**NFR2 (no false positives).** A command that satisfies FR4 is never blocked,
regardless of what follows the `&&`. A command run from `W == E` that does not
start with `cd` is never blocked.

**NFR3 (zero runtime dependencies).** The hook uses Python 3 stdlib only
(`json`, `os`, `re`, `shlex`, `sys`). No third-party packages, no subprocess
calls, no network access. **Exception:** worktree detection performs read-only
filesystem access (stat + reading a `.git` file). It remains subprocess-free
and network-free.

**NFR4 (low latency).** The hook completes in well under the 5-second timeout
registered in `hooks.json`. All logic is in-process; there are no forked
subprocesses.

**NFR5 (dual-channel messaging).** Block messages go to stderr so the terminal
user sees them. PostToolUse warnings include both `hookSpecificOutput` (agent
context channel) and `systemMessage` (user-visible sidebar). Both channels
must carry the warning so neither agent nor user is blind to drift.

**NFR6 (security of the `&&` invariant).** The root-anchored form permits
`cd E && cmd` because `&&` guarantees `cmd` runs only if the `cd` succeeds.
The hook must reject `;` and `||` separators to uphold this invariant.
Exact path match only — no traversal, no normalization, no prefix matching.

**NFR7 (portability).** `hooks.json` references the script via
`${CLAUDE_PLUGIN_ROOT}` so the hook resolves correctly regardless of where
the plugin is installed in the user's plugin cache.

## Design Decisions

Five decisions give the guard its shape and are argued here. The rest are
grouped by the node that argues them: the conclusion is here, the argument —
what was weighed, what was rejected, and why — is one hop away.

### (a) Block vs warn at PreToolUse

**Decision:** Block (exit 2) on PreToolUse, not merely warn.

**Rationale:** Warnings are ignored in practice — the agent continues issuing
commands from a drifted cwd and compounds the confusion. The requirement that
drives the block is stated in the hook's module docstring: "read-only commands
from wrong cwd actively mislead the agent about context." A misleading read
(e.g., `git status` from inside a submodule) is not harmless; it produces
incorrect information that the agent treats as authoritative. A hard block
eliminates the failure mode entirely rather than attenuating it.

**Alternatives considered:** Emit a warning and allow. Rejected because the
agent reliably ignored non-blocking warnings and proceeded to accumulate
confusion.

### (b) Proactive cd block — FR5, even at project root

**Decision:** Block any `cd` command that is not the sanctioned root-anchored
form, including when `W == E`.

**Rationale:** Blocking only when `W != E` would allow a bare `cd subdir` issued
from project root, causing drift that the PostToolUse warn then catches after
the fact. FR5 intercepts the drift before it happens. The leading-`cd` pattern
is the most common cause of drift, so stopping it unconditionally (whether
`W == E` or not) removes the most common failure path outright. The PostToolUse
warn remains as a backstop for paths the PreToolUse gate cannot intercept (e.g.,
`pushd`).

**Alternatives considered:** Allow `cd subdir` from root and rely on
PostToolUse warn. Rejected: the agent then executes at least one command
sequence from a drifted cwd before the warning fires, which is the failure
mode we are trying to eliminate. The proactive block costs nothing (there is
no legitimate need for persistent cwd change) and eliminates the whole class.

### (e) Dual-mode (Pre + Post) rather than a single event

**Decision:** Hook both `PreToolUse` and `PostToolUse`.

**Rationale:** `PreToolUse` alone cannot stop drift from commands it does not
know will drift. `pushd`, for example, is not detected by `_LEADING_CD`. The
PostToolUse handler is a backstop: it detects drift after the fact and warns,
leaving the agent aware that the next PreToolUse command will be blocked until
it restores cwd. Dual-mode is a deliberate concession that PreToolUse blocks
cannot be complete, and the warning reaches both channels (NFR5) so neither
agent nor user is blind to drift the gate let through.

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

**Rationale:** The original name (`submodule-safety.py`) reflected the specific
trigger that motivated the hook's creation: agents drifting into git submodule
directories. The hook was generalized within a day to cover any cwd drift, not
only submodule descent, so the name was a misnomer for its entire life in
agent-core. Extracting the hook into a standalone plugin was the natural point
to rename it to match what it actually does.

**Alternatives considered:** Keep the name for continuity. Rejected: the name
actively misleads about the scope of the guard.

### Argued in the nodes

**The command matchers** — what each regex accepts, and the two invariants
`_is_cd_to_root` never loosens. [matchers.md](references/matchers.md)

- **(c)** — only `&&` is accepted after the root-anchored `cd`; `;` and `||`
  never qualify
- **(d)** — the root is matched literally: no normalization, no traversal, no
  prefix match
- **(j)** — redirections are tolerated between a `cd` target and its `&&`; an
  embedded `cd` right after a top-level separator or compound-body keyword is
  blocked, by a regex over a quote/heredoc/paren-masked command rather than a
  parser

*Rejected:* `;` for ergonomics · normalizing both sides before matching ·
arbitrary tokens between the target and `&&` · rewriting an embedded `cd` into
a subshell · leaving embedded `cd` to PostToolUse · a tree-sitter bash parse.

**Worktrees and the unusable root** — how `E` is found, and what happens when
it is gone or was never set. [worktrees.md](references/worktrees.md)

- **(h)** — a single effective root `E`, the enclosing worktree detected from
  the on-disk `.git` linkage; a `cd` back to the main root while a worktree is
  active is blocked in favor of `ExitWorktree`
- **(k)** — the hook fails open whenever `E` is unusable, whether it no longer
  exists on disk or was never set, shouting what is wrong rather than blocking
  with an unfollowable hint; a `$CLAUDE_PROJECT_DIR` that is itself a linked
  worktree is a plain root, with no self-destruct guard

*Rejected:* a `worktree` payload field · the `.claude/worktrees/` path
convention · accepting both roots · the hook setting the Bash cwd · a
`mkdir -p E && cd E` restore · re-anchoring `E` to the fallback dir · keeping
the shape-2 guard.

**The restore rewrite** — the two shapes rewritten instead of blocked, and why
the restore is a flat trailing line.
[restore-rewrite.md](references/restore-rewrite.md)

- **(i)** — `cd <dir> && <cmd>` from root is rewritten with a blank line and
  `cd <E>` appended, never blocked and never wrapped in `( … )`: a subshell
  defeats the sandbox `excludedCommands` matcher and mangles a trailing heredoc
- **(l)** — a `set -e` script with an embedded `cd` gets the same restore;
  errexit is the trigger, not the guarantee, because it is inert under the Bash
  tool

*Rejected:* keep blocking · rewrite silently · also rewrite `;`/`||` and bare
`cd` · rewrite from a drifted cwd · a `( … )` subshell wrap, in either shape ·
dropping the rewrite · requiring only that `set -e` precede the first `cd` ·
rewriting every `set -e` command · honoring `set -e` from drift · mirroring
FR5a's cross-root exclusion · dropping FR5c · generalizing to every
embedded-`cd` script.

## Limitations

- **`pushd`/`popd` are not intercepted.** The `_LEADING_CD` regex only matches
  commands whose first token is `cd`. `pushd subdir` will cause drift that
  PostToolUse will warn about but PreToolUse will not block.

- **Contrived `cd E && cd subdir && cmd` passes the PreToolUse gate.** The
  `_is_cd_to_root` regex matches the entire command against the root-anchored
  pattern. A command starting with `cd E &&` satisfies FR4, so the rest of the
  pipeline executes — including a second `cd subdir`. PostToolUse is the
  backstop for this case. Note the embedded-cd block (FR5b / decision (j))
  catches the *non*-leading shape (`<setup> && cd subdir && …`) but deliberately
  does **not** touch this leading-`cd E &&` form, which FR4 allows by design.

- **Embedded-cd detection is a masked regex, not a parser.** FR5b flags a `cd`
  only when it immediately follows a top-level separator or a `then`/`do`/`{`,
  after quoted strings, comments, heredoc bodies and parenthesised regions are
  blanked. It misses a `cd` reached another way — `eval`, `builtin cd`,
  `command cd`, `\cd`, an aliased or function `cd`, `pushd` — and a function
  body that `cd`s blocks on definition, whether or not the function is called.
  An unterminated quote blanks to the end and the command falls through
  unblocked (bash refuses it anyway). Every residual error is a missed block
  or a spurious one, never a loosened root anchor; PostToolUse remains the
  ultimate backstop.

- **FR5c triggers on errexit only from the first statement.** A `set -e` reached
  any other way — a later statement (`foo; set -e; cd x`), or one whose spelling
  the small matcher does not recognize — is not treated as a rewritable script;
  it falls through to the FR5b block and the agent re-forms. This is the
  conservative direction (a missed rewrite only blocks). Errexit itself is inert
  under the Bash tool (decision (l)), so nothing in FR5c depends on it firing.

- **The restore is a trailing statement, not a construction.** FR5a/FR5c keep
  cwd at `E` by appending `cd <E>`; a tail that `exec`s, `exit`s, or kills the
  shell skips it and drifts. This is the same class as `pushd` drift and is
  caught by PostToolUse. The alternative — a `( … )` subshell — was rejected in
  decision (i) because it defeats the sandbox exclusion matcher.

- **Single-root only.** The hook is keyed to a single `$CLAUDE_PROJECT_DIR`.
  Multi-root setups (nested worktrees, monorepo sub-roots) are not supported.
  Only one root is enforced; work in a sibling worktree will be blocked if its
  path differs from `$CLAUDE_PROJECT_DIR`.

- **No quote normalization in `_LEADING_CD`.** The leading-cd regex
  `^cd(?:\s|;|&|$)` detects the unquoted `cd` builtin. A quoted `'cd'` or
  `\cd` invocation is not matched, though those forms are unusual in practice.

- **Worktree detection reads the filesystem.** Unlike the rest of the hook it is
  not pure-stdin; it stats ancestors of `cwd` and reads one `.git` file. A
  worktree whose `.git` linkage does not resolve under
  `$CLAUDE_PROJECT_DIR/.git` (e.g. a different repo) is treated as drift, not as
  a valid anchor.

- **The guard disables itself for the rest of a session once `E` is unusable.**
  Whether `E` no longer exists on disk (FR7a) or was never set (FR7b), fail-open
  allows every command; neither state repairs itself, so there is no
  re-anchoring within that session. This is intended — a worktree session that
  removed its own worktree is winding down at the main repo, and a session with
  no `$CLAUDE_PROJECT_DIR` cannot be given one from inside — and the PostToolUse
  notice tells the human which state it is and to restart.

## History

Write-time records of each change — what moved and the reasoning available at
the time — live in [changelog.md](changelog.md), one file per entry. This
document states what the plugin *is*; the changelog states how it got there.
