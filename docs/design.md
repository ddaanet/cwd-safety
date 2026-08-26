# cwd-safety — Design

Living design document. States what the plugin *is* and why it has this shape.
Present tense throughout: when a decision is overturned it is rewritten here in
place, and the reversal gets a dated entry in [changelog.md](changelog.md).

`cwd-safety` is a Claude Code plugin that keeps the agent's Bash working
directory at project root. It fires a single Python hook
(`scripts/cwd-safety.py`) on both `PreToolUse(Bash)` and
`PostToolUse(Bash)`. The pre-use side blocks any command that would cause or
exploit cwd drift before it runs — or, for two ergonomic cases issued from
root (a `cd <subdir> && <cmd>`, and a `set -e` script with an embedded `cd`),
rewrites it in place to a
non-persisting subshell rather than blocking; the post-use side warns after
drift is detected (a backstop for cases the pre-use gate cannot intercept).
Together they enforce a hard boundary: the agent executes Bash from project
root or not at all.

## Functional Requirements

**FR1.** The hook fires on `PreToolUse(Bash)` and `PostToolUse(Bash)` only.
No other tool events trigger it.

**FR2.** The hook reads `$CLAUDE_PROJECT_DIR` for the project root and `cwd`
from hook stdin. The **effective root** `E` is the enclosing git-worktree root
when `cwd` is inside a worktree of `$CLAUDE_PROJECT_DIR` — detected from the
on-disk `.git` linkage (a worktree's `.git` is a file whose `gitdir:` resolves
under `$CLAUDE_PROJECT_DIR/.git`) — otherwise `$CLAUDE_PROJECT_DIR`. All
decisions below are made against `E`. When `$CLAUDE_PROJECT_DIR` is *itself* a
linked worktree (its own `.git` file reads `gitdir: <main>/.git/worktrees/<name>`
— the shape of a background worktree session), `_worktree_root` finds no
*enclosing* worktree, so `E = $CLAUDE_PROJECT_DIR` and the hook governs it as an
ordinary root: a `cd` to the main repo is a benign subdir-style subshell rewrite,
not a cross-root block. See decision (k).

**FR3 (allow-silent).** On `PreToolUse`: if `W == E` and the command `C` does
not begin with `cd`, the hook exits 0 silently (no output, no block).

**FR4 (root-anchored allow).** On `PreToolUse`: the command forms `cd E`,
`cd "E"`, `cd 'E'`, `cd E && <rest>`, `cd "E" && <rest>`, and
`cd 'E' && <rest>` — where `E` is an exact match for the effective root,
and `&&` is the only accepted separator — are allowed from any `W`. No other
separator (`; `, `||`) qualifies. **Redirections may sit between the target and
the `&&`** (`cd E 2>&1 && <rest>`, `cd E >log && <rest>`, `cd E 2>/dev/null`): a
redirection does not change the cd-first `&&` semantics, so it neither blocks the
allow nor licenses a non-`&&` separator (`cd E 2>&1; <rest>` still blocks). See
decision (j).

**FR5 (proactive cd block).** On `PreToolUse`: any command whose first token
is `cd` (i.e., `cd`, `cd …`, `cd;…`, or `cd&&…`, but not `cdfoo`) that does
not satisfy FR4 is blocked with exit 2 and a stderr message — even when
`W == E`. The message names the effective root and offers three sanctioned
alternatives: absolute paths, the `cd E && <command>` form, and a
non-persisting subshell `(cd subdir && <command>)`. When a worktree is active,
this includes a `cd` back to `$CLAUDE_PROJECT_DIR`: leaving a worktree is done
with the `ExitWorktree` tool, not a raw `cd`. The block message is
worktree-aware and names `ExitWorktree`.

**FR5a (subshell rewrite).** On `PreToolUse`, when `W == E`: a command of the
form `cd <dir> && <rest>` — a leading `cd` to a single directory argument joined
by `&&` to a non-empty tail, not satisfying FR4 — is **not** blocked. Instead the
hook returns `permissionDecision: "allow"` with `hookSpecificOutput.updatedInput`
replacing the command with `(cd <dir> && <rest>)`, scoping the directory change
to a non-persisting subshell so cwd stays at `E` by construction. The `<dir>`
argument may contain spaces when double-quoted, single-quoted, or
backslash-escaped, and may be followed by redirections before the `&&`
(`cd sub 2>&1 && <rest>` → `(cd sub 2>&1 && <rest>)`; see FR4 / decision (j)). The rewrite is announced on both channels — never silent — but
**neither note echoes the command** (Claude Code already surfaces the rewritten
`updatedInput`, so re-echoing is bloat): `additionalContext` tells the agent cwd
did not persist and recommends the *wrapped* `(cd <dir> && <command>)` form for
follow-ups (recommending the unwrapped form would only re-trigger the rewrite);
`systemMessage` is a terse "wrapped command in a subshell". Exclusions, all of
which fall through to the FR5 block: a bare `cd <dir>` with no `&&` tail; a
pathless `cd && …`; two-bareword `cd a b && …` (not a single directory
argument); the `;` and `||` separators; and — when a worktree is active — a `cd`
to `$CLAUDE_PROJECT_DIR` (a cross-root transition governed by `ExitWorktree`, not
a subdir descent; the target is compared de-quoted so a quoted/spaced main-root
path is excluded too). From a drifted cwd (`W != E`) the same `cd <dir> && <rest>`
is still blocked (FR6): the agent must restore `E` first, since a subshell from
the wrong cwd would run the tail from the wrong cwd.

**FR5b (embedded-cd block).** On `PreToolUse`, when `W == E`: a command that is
not itself a leading `cd` but contains a `cd` running in the current shell right
after a top-level sequencing operator (`&&`, `||`, `;`, `&`, or a newline) — e.g.
`mkdir -p tools && cd tools && …`, `echo hi; cd sub` — is blocked with the FR5
message (which recommends the `(cd sub && …)` subshell form). The `cd` must
*immediately* follow the separator, so a `(cd sub && …)` subshell and a
`foo | cd sub` pipeline (single `|`, its `cd` runs in a subshell) are never
caught. This is a narrow drift detector, not a shell parser: a quoted literal
containing `&& cd` is a known, accepted false positive (it only causes a block).
The contrived leading form `cd E && cd sub && …` is *not* covered — it satisfies
FR4 and is allowed (see Limitations). See decision (j).

**FR5c (`set -e` subshell rewrite).** On `PreToolUse`, when `W == E`: a command
whose **first effective statement** (after any leading blank or `#`-comment
lines) is a `set` builtin that **enables errexit** — a `-`flag cluster
containing `e` (`set -e`, `set -eu`, `set -euo pipefail`, `set -ex`) or
`set -o errexit` — and which would otherwise be blocked by the embedded-cd
detector (FR5b), is **not** blocked. Instead the hook returns
`permissionDecision: "allow"` with `hookSpecificOutput.updatedInput` replacing
the command `C` with `(C)`, scoping the whole script to a non-persisting
subshell. Rationale: with errexit active *from the first statement*, a failed
`cd` aborts the subshell before the tail runs — the same cd-first guarantee
`&&` provides (decision (c)) — so a `;`/newline-separated `set -e` script is as
safe to subshell as FR5a's `cd <dir> && <cmd>`, and cwd non-persistence is
guaranteed by the `( … )`. The rewrite is announced on both channels, never
silent: a dedicated `additionalContext` note names `set -e` and states cwd did
not persist; `systemMessage` is a terse "wrapped set -e script in a subshell."
Neither echoes the command (Claude Code surfaces the rewritten `updatedInput`).
Exclusions, all of which fall through to the FR5b block: the `+` forms
(`set +e`, `set +o errexit`) and errexit-absent forms (`set -u`,
`set -o pipefail`, bare `set`) — errexit is not guaranteed before the `cd`; a
`set` that is *not* the first statement (`foo; set -e; cd x`) — same reason; and
`setup && …` (matched with `\b`, not mistaken for `set`). It fires only when an
embedded `cd` is present: a `set -e` script with no `cd` stays allow-silent
under FR3, untouched. Unlike FR5a it does **not** carve out a cross-root
`cd $CLAUDE_PROJECT_DIR` while a worktree is active — the embedded-cd detector
does not parse targets, and the subshell keeps cwd in the worktree regardless (a
transient command from main, not a persistent transition); a deliberate,
documented divergence. From a drifted cwd (`W != E`) the same script is still
blocked (FR6): restore `E` first. See decision (l).

**FR6 (drift block).** On `PreToolUse`: if `W != E` and the command is not
the root-anchored form (FR4) and does not trigger FR5, the hook blocks with
exit 2 and a stderr message showing the current `W` and the restore command
`cd E`.

**FR7 (post-use warn).** On `PostToolUse`: if `W != E`, the hook emits a JSON
payload containing `hookSpecificOutput.additionalContext` and `systemMessage`
with a warning showing `W` and the restore command. If `W == E`, the hook
exits 0 silently.

**FR7a (fail-open on a deleted root).** When `E` is non-empty but no longer
exists on disk (`os.path.isdir(E)` is false — a worktree removed out from under
the session, an external `rm`, etc.), the guard's contract "keep cwd at `E`" is
unsatisfiable. On `PreToolUse` the hook then allows *every* command silently,
before all other rules (FR3–FR6) — even a leading `cd` — so the agent can work
from wherever the shell fell back to. On `PostToolUse` it emits a *replacement*
warning (both channels) that names `E`, states the guard is disabled for the
session, and says to restart — superseding the FR7 `cd E` hint, which is
impossible once `E` is gone. `E == ""` (no `$CLAUDE_PROJECT_DIR`) is a different
degenerate state and does not trigger fail-open. Because `E` stays gone, the
guard is effectively off for the remainder of the session. See decision (k).

**FR8 (wiring).** `hooks/hooks.json` registers `scripts/cwd-safety.py` via
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cwd-safety.py` for both
`PreToolUse[Bash]` and `PostToolUse[Bash]` with a 5-second timeout.

**FR9 (unknown events).** If the hook is invoked with a `hook_event_name`
other than `PreToolUse` or `PostToolUse` it exits 0 silently (forward
compatibility).

## Non-Functional Requirements

**NFR1 (determinism).** Given identical `W`, `R`, and `C`, the hook always
produces the same decision. No randomness, no external I/O, no mutable state
beyond stdin/stdout/stderr. Worktree detection reads the filesystem (`os.path`
stat calls and one `.git` file); the hook is deterministic *given filesystem
state*. It performs no network access and spawns no subprocess.

**NFR2 (no false positives).** A command that satisfies FR4 is never blocked,
regardless of what follows the `&&`. A command run from `W == R` that does not
start with `cd` is never blocked.

**NFR3 (zero runtime dependencies).** The hook uses Python 3 stdlib only
(`json`, `os`, `re`, `sys`). No third-party packages, no subprocess calls, no
network access. **Exception:** worktree detection performs read-only filesystem
access (stat + reading a `.git` file). It remains subprocess-free and
network-free.

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
form, including when `W == R`.

**Rationale:** Blocking only when `W != R` would allow a bare `cd subdir` issued
from project root, causing drift that the PostToolUse warn then catches after the
fact. FR5 intercepts the drift before it happens. The leading-`cd` pattern is the
most common cause of drift, so stopping it unconditionally (whether `W == R` or
not) removes the most common failure path outright. The PostToolUse warn remains
as a backstop for paths the PreToolUse gate cannot intercept (e.g., `pushd`).

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
explicitly in `scripts/cwd-safety.py`'s module docstring, alongside the
`_is_cd_to_root` regex that enforces it.

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
agent-core. Extracting the hook into a standalone plugin was the natural point to
rename it to match what it actually does.

**Alternatives considered:** Keep the name for continuity. Rejected: the name
actively misleads about the scope of the guard.

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

**Alternatives considered:** (1) Trust a `worktree` payload field — rejected, it
does not exist. (2) `.claude/worktrees/` path convention — rejected, arbitrary
and relocated worktrees defeat it. (3) Accept both `R` and the worktree root —
rejected; `ExitWorktree` makes exit-then-merge the clean path. (4) Have the hook
set the Bash cwd — impossible; hook output cannot redirect the tool's cwd.

### (i) Rewrite `cd <subdir> && <cmd>` to a subshell instead of blocking

**Decision:** At the effective root, a `cd <path> && <rest>` command is rewritten
in place to the non-persisting subshell `(cd <path> && <rest>)` via a
`PreToolUse` `updatedInput`, rather than blocked (FR5a). Bare `cd <path>`, the
`;`/`||` separators, a cross-root `cd` to `$CLAUDE_PROJECT_DIR` while in a
worktree, and the same command from a drifted cwd all remain blocked.

**Rationale:** Under decision (b) a `cd subdir && cmd` was blocked, and the block
message offered `(cd subdir && cmd)` as the sanctioned alternative — so the agent
spent a turn reading the block and reissuing the exact form the hook already knew
it wanted. `PreToolUse` hooks can rewrite the tool input
(`hookSpecificOutput.updatedInput`), so the hook can produce that form directly
and save the turn. Crucially this is **free on the security axis**: a subshell
cannot mutate the parent shell's cwd, so the no-persistence invariant that the
whole plugin enforces holds by construction — exactly as it would after the block
+ reissue. This is *not* the security-vs-ergonomics trade that decision (d)
rejected (normalization, which would weaken the exact-match guarantee); it is
ergonomics at no security cost.

The genuine cost is a different one: this is the first behavior that **mutates**
the agent's command rather than only allowing, blocking, or warning. Mutation
touches auditability (the executed command differs from what the transcript shows
the agent wrote) and least-surprise. That cost is paid down by making the rewrite
**never silent** — `additionalContext` (agent) and `systemMessage` (user) both
fire, so neither party is blind to the substitution and a follow-up command that
assumed persistence is corrected by context rather than by a confusing wrong-cwd
result. Neither note re-echoes the command: Claude Code already surfaces the
rewritten `updatedInput`, so the agent note instead states cwd did not persist and
recommends the *wrapped* `(cd <dir> && <command>)` form for follow-ups — pointing
at the unwrapped form would just trigger another rewrite and another
notification — and the user note is a terse one-liner.

The rewrite is deliberately narrow. It fires only from `W == E` (from drift, the
only sanctioned command is the `cd E` restore; a subshell from the wrong cwd
would run the tail from the wrong cwd). It requires a single directory argument
and an `&&` tail, so bare `cd subdir` — the persistent-drift *intent* — still hits
the FR5 block and still teaches. The directory may carry spaces when quoted or
backslash-escaped (a verbose `_CD_AND` regex matches one shell argument: a
"double"/'single'-quoted string or a bareword of plain chars and `\`-escapes), so
real-world paths like `cd "my dir" && …` are not needlessly blocked; this is
non-security-critical sugar — a wrong match merely subshells (still no-persist) or
blocks (agent reissues), so the looser parsing here does not touch the
exact-match security matcher `_is_cd_to_root` (decision (d)), which is untouched.
It excludes `;`/`||` for the same reason FR4 does (decision (c)). And while a
worktree is active it excludes `cd $CLAUDE_PROJECT_DIR`: that is a transition
between the two managed roots, governed by `ExitWorktree` (decision (h), "no
single Bash call legitimately needs both roots"), not a subdir descent —
subshelling it would back-door exactly the cross-root operation decision (h)
forbids. The exclusion compares the *de-quoted* target to `$CLAUDE_PROJECT_DIR`,
so a quoted or escaped main-root path is caught just like a bare one.

**Alternatives considered:** (1) Keep blocking and rely on the agent to reissue —
rejected, it wastes a turn for no safety gain now that `updatedInput` exists. (2)
Rewrite silently — rejected on auditability/least-surprise grounds; the dual-
channel announcement is mandatory. (3) Also rewrite `cd <subdir>; <cmd>` /
`|| <cmd>` and bare `cd <subdir>` — rejected: `;`/`||` break the cd-first
invariant the project rejects everywhere else, and a bare `cd` has no tail to
scope, so wrapping it (`(cd subdir)`) is a no-op that would suppress the teaching
block. (4) Rewrite from a drifted cwd too — rejected: it would run the tail from
the wrong directory; restore must come first.

### (j) Redirect tolerance and the narrow embedded-cd block

**Decision:** (1) The root-anchored allow (FR4) and the subshell rewrite (FR5a)
tolerate shell redirections between the `cd` target and the `&&`. (2) An embedded
`cd` running in the current shell right after a top-level separator (FR5b) is
blocked, even though `cd` is not the command's leading token.

**Rationale:** Both address natural command forms the matchers mishandled.
`cd <dir> 2>&1 && <cmd>` — capturing the `cd`'s own diagnostics — was blocked
because a redirection sat between the path and the `&&`; yet a redirection cannot
change the cd-first guarantee `&&` provides (the tail still runs only if `cd`
succeeds), so tolerating it is free on the security axis. The redirection grammar
is deliberately bounded — an fd-dup (`2>&1`, no filename) or a filename token that
excludes `& | ; < > ( )` — so it can never swallow the `&&` or introduce a second
command; a non-`&&` separator (`cd E 2>&1; <cmd>`) still blocks (decision (c)
holds). The exact path match (decision (d)) is untouched: the redirections sit
*after* the exactly-matched path.

The embedded-cd block narrows the "contrived `cd R && cd subdir` passes" gap for
its most common real shape, `<setup> && cd <subdir> && <work>`, which drifts from
root and was previously only caught after the fact by PostToolUse. It is a
deliberately *narrow* regex, not a shell parser: requiring the `cd` to immediately
follow the separator excludes the sanctioned `(cd sub && …)` subshell and pipe
subshells for free, and the residual false positives (a quoted `"&& cd"` literal;
a no-op `echo x && cd E`) only ever *block* — the agent re-forms the command — so
they cost ergonomics, never safety. This is a targeted retreat from the
"don't chase an exhaustive blocklist" non-goal, not an abandonment of it:
PostToolUse remains the backstop for `pushd`, `source`, `&& cd` inside a
root-anchored FR4 command, and everything the regex cannot see.

**Alternatives considered:** (1) Allow arbitrary tokens (not just redirections)
between the target and `&&` — rejected: junk like `cd E ; rm && cmd` would break
the cd-first invariant. (2) Rewrite the embedded `cd` into a subshell rather than
block — rejected: correctly splitting a chain around quotes and nested subshells
needs a real parser (chosen over in brainstorming). (3) Leave embedded `cd` to
PostToolUse entirely — rejected: the drift executes at least one command from the
wrong cwd before the warning fires, the failure mode the block exists to prevent.

### (k) Fail open on a deleted root; drop the shape-2 self-destruct guard

**Decision:** (1) When the effective root `E` does not exist on disk, the hook
fails open — PreToolUse allows everything silently, PostToolUse emits a "guard
disabled — restart" notice (FR7a). (2) The `_worktree_main_root` special-case
(which treated a `$CLAUDE_PROJECT_DIR` that is itself a linked worktree as a
worktree, blocking `cd <main>` in favor of `ExitWorktree`) is removed; that shape
is now governed as a plain root.

**Rationale:** Both address the self-destruct trap: a background worktree session
runs `cd <main> && git worktree remove --force <self>` and deletes its own cwd.
The shell falls back to the main repo, but `E` still points at the now-gone
worktree, so every command hits the FR6 drift block, the `cd E` restore no-ops
(target gone), and the Bash tool deadlocks. The guard's whole contract is "keep
cwd at `E`"; once `E` is gone that contract is meaningless, and
the only coherent behavior is to step aside. Fail-open covers *every* deletion
vector — self-removal, another session's `git worktree remove`, `git worktree
prune`, an external `rm` — because it keys on the on-disk fact, not the mechanism.

A `_worktree_main_root` guard once tried to *prevent* this trap by blocking the
self-`cd <main>`. Its advice was a dead end: `ExitWorktree` is a no-op for a
background worktree session (it only exits a worktree the current session created
via `EnterWorktree`), so the guard steered the agent to a tool that does nothing
while blocking the one working cleanup path
(`cd <main> && git worktree remove <self>`). Once fail-open makes the trap
survivable, such a guard only forbids legitimate post-merge cleanup, so there is
none. The shape behaves as a plain root: `cd <main> && …` is a benign
non-persisting subshell rewrite, and drift blocks with a plain `cd <E>` hint.

**Consciously re-accepted:** an *accidental* self-destruct is no longer
hard-blocked. Mitigations: `git worktree remove` refuses a dirty worktree without
`--force`; `--force` is an explicit opt-in to destruction; the real safety in
practice is the agent asking the user before a destructive cleanup; cwd-safety was
never a data-loss guard; and fail-open makes the outcome recoverable regardless.

**Alternatives considered:** (1) Allow a narrow `mkdir -p E && cd E` restore —
rejected: it recreates a hollow non-git directory, and the shell is *already* at a
valid dir (main), so there is nothing to restore to. (2) Detect the deleted-root
state and re-anchor `E` to the fallback dir — rejected: needs state the hook does
not have and the path heuristics decision (h) already refused. (3) Keep the
shape-2 guard and document `ExitWorktree` as the exit — rejected: `ExitWorktree`
is a no-op for this session shape, so the documented exit does not exist.

### (l) Rewrite a `set -e` script to a subshell, like `cd <dir> && <cmd>`

**Decision:** At the effective root, a command whose first statement enables
shell errexit (`set -e` / variant) and that contains an embedded `cd` — which
FR5b would otherwise block — is rewritten in place to the non-persisting
subshell `(C)` via a `PreToolUse` `updatedInput` (FR5c), the same treatment
FR5a gives `cd <dir> && <cmd>`. The `+` forms, errexit-absent `set`, a non-first
`set`, and the same script from a drifted cwd all remain blocked.

**Rationale:** FR5a rests on `&&`: `cd <dir> && <cmd>` is safe to subshell
because `&&` guarantees the tail runs only if the `cd` succeeds (decision (c)),
so a subshell can never run the tail from the wrong cwd. `set -e` provides *the
same guarantee by a different mechanism*: with errexit active, a failed `cd`
aborts the script before the tail runs. So the everyday shape

```
set -e
cd tools
make build
```

is exactly as safe to scope to `(set -e; cd tools; make build)` as
`cd tools && make build` is — the `;`/newline separators that decision (c)
rejects for the *root-anchored* form are neutralized here by errexit, precisely
the way `&&` neutralizes them. cwd non-persistence itself is guaranteed by the
`( … )`, identical to FR5a; errexit is what makes the *sequential* form safe to
wrap. Blocking these scripts and making the agent hand-wrap a multi-line script
into a single `(cd … && …)` chain was the friction FR5a already removed for the
one-liner; FR5c removes it for the fail-fast-script idiom.

The safety hinge is that errexit must be active *before* the `cd`. The
sufficient, conservative condition chosen is "`set -e` is the **first**
statement" (modulo leading blank/comment lines — a shebang-like first line is a
comment): then every later `cd` is protected. This is stricter than "`set -e`
appears somewhere before the first `cd`", and deliberately so — it needs no
position scan and no shell parse, and the failure direction is safe (a `set -e`
that is not first simply falls through to the FR5b block, and the agent
re-forms). The errexit matcher is a small regex, not a parser: it inspects only
the first statement's own options and anchors on `-` so `set +e` cannot pass. As
with FR5a's `_CD_AND`, a wrong match is not security-critical — it can only
subshell (still no-persist) or block (agent reissues) — so it does not touch the
exact-match matcher `_is_cd_to_root` (decision (d)).

Like FR5a this is a command **mutation**, so the same auditability cost applies
and is paid down the same way: the rewrite is never silent — a dedicated agent
note (naming `set -e`) and a terse user note both fire, so a follow-up that
assumed cwd persisted is corrected by context. And like FR5a it fires only from
`W == E` and only to *replace a block*: a `set -e` script with no `cd` is left
allow-silent (FR3), unmutated.

**Consciously diverged from FR5a:** the cross-root exclusion is *not* mirrored.
FR5a refuses to subshell a leading `cd $CLAUDE_PROJECT_DIR` in a worktree
(decision (i), governed by `ExitWorktree`). FR5c's embedded-cd detector does not
parse the `cd` target — adding that parse would re-introduce exactly the
shell-parsing the project avoids — and the divergence is harmless: the wrap is a
non-persisting subshell, so an embedded `cd <main>` runs one transient command
from main and cwd returns to the worktree. There is no persistent cross-root
transition to forbid.

**Alternatives considered:** (1) Require only that `set -e` precede the first
`cd` (not be first) — rejected: needs an offset scan for a case (`set -e` after
non-cd setup) rare enough that falling through to the block is fine. (2) Wrap
*every* `set -e` command, `cd` or not — rejected: needless mutation and a
notification for scripts FR3 already allows silently, and it would suppress the
parent shell's own errexit as a side effect. (3) Also honor `set -e` from a
drifted cwd — rejected: the tail would run from the wrong cwd; restore `E`
first, as FR5a requires. (4) Mirror FR5a's cross-root exclusion — rejected: it
needs embedded-target parsing for a case the subshell already makes cwd-safe.

## Limitations

- **`pushd`/`popd` are not intercepted.** The `_LEADING_CD` regex only matches
  commands whose first token is `cd`. `pushd subdir` will cause drift that
  PostToolUse will warn about but PreToolUse will not block.

- **Contrived `cd R && cd subdir && cmd` passes the PreToolUse gate.** The
  `_is_cd_to_root` regex matches the entire command against the root-anchored
  pattern. A command starting with `cd R &&` satisfies FR4, so the rest of the
  pipeline executes — including a second `cd subdir`. PostToolUse is the
  backstop for this case. Note the embedded-cd block (FR5b / decision (j))
  catches the *non*-leading shape (`<setup> && cd subdir && …`) but deliberately
  does **not** touch this leading-`cd R &&` form, which FR4 allows by design.

- **Embedded-cd detection is a regex, not a parser.** FR5b flags a `cd` only when
  it immediately follows a top-level separator. It misses a `cd` reached another
  way (inside `$(…)` that escapes to the parent, an aliased/function `cd`) and
  false-positives on a quoted `"&& cd"` literal — the latter only blocks, so it is
  safe if occasionally inconvenient. PostToolUse remains the ultimate backstop.

- **FR5c honors errexit only from the first statement.** A `set -e` reached any
  other way — a later statement (`foo; set -e; cd x`), an errexit inherited from
  a caller, or one whose spelling the small matcher does not recognize — is not
  treated as a wrappable script; it falls through to the FR5b block and the agent
  re-forms. This is the conservative direction (a missed wrap only blocks). The
  wrap also does not audit errexit's own escape hatches (a `cd` masked by
  `|| true`, an `if`, or a pipeline will not abort under `set -e`) — but those
  are the user's explicit choice and remain cwd-safe, since the wrap is a
  non-persisting subshell regardless.

- **Single-root only.** The hook is keyed to a single `$CLAUDE_PROJECT_DIR`.
  Multi-root setups (nested worktrees, monorepo sub-roots) are not supported.
  Only one root is enforced; work in a sibling worktree will be blocked if its
  path differs from `$CLAUDE_PROJECT_DIR`.

- **No quote normalization in `_LEADING_CD`.** The leading-cd regex
  `^cd(?:\s|;|&|$)` detects the unquoted `cd` builtin. A quoted `'cd'` or
  `\cd` invocation is not matched, though those forms are unusual in practice.

- **Worktree detection reads the filesystem.** Unlike the rest of the hook it
  is not pure-stdin; it stats ancestors of `cwd` and reads one `.git` file. A
  worktree whose `.git` linkage does not resolve under `$CLAUDE_PROJECT_DIR/.git`
  (e.g. a different repo) is treated as drift, not as a valid anchor.

- **The guard disables itself for the rest of a session after root deletion.**
  Once `E` no longer exists on disk, fail-open (FR7a / decision (k)) allows every
  command; `E` never comes back, so there is no re-anchoring within that session.
  This is intended — a worktree session that removed its own worktree is winding
  down at the main repo — and the PostToolUse notice tells the human to restart to
  re-establish a valid root.
## History

Write-time records of each change — what moved and the reasoning available at
the time — live in [changelog.md](changelog.md), one file per entry. This
document states what the plugin *is*; the changelog states how it got there.
