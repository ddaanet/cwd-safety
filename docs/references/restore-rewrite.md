# The restore rewrite

FR5a and FR5c in full: the two shapes issued from the effective root that the
hook rewrites — appending a newline and `cd <E>` — instead of blocking, what
the rewrite announces, what it excludes, and the decisions arguing for that
shape. The conclusions are in [design.md](../design.md); this file is what you
need while building or debugging `_rewrite_with_restore` and its two triggers.
The matchers that recognize the triggers are in [matchers.md](matchers.md);
the worktree cross-root exclusion is grounded in [worktrees.md](worktrees.md).

- Rewrite, never block or wrap — **(i)** `cd <dir> && <cmd>` from root is
  rewritten with a newline and `cd <E>` appended, never blocked and never
  wrapped in `( … )` · **(l)** a `set -e` script with an embedded `cd` gets the
  same restore; errexit is the trigger, not the guarantee, because it is inert
  under the Bash tool

---

## Mechanism

Both triggers go through `_rewrite_with_restore`, which returns
`permissionDecision: "allow"` with `hookSpecificOutput.updatedInput` replacing
the command `C` with `C`, a newline, and `cd <E>` (`E` shell-quoted). The
restore is a separate line, never a `( … )` subshell and never a `;`: a
subshell hides the command from the sandbox's `excludedCommands` matcher, and
a newline keeps a trailing heredoc or `# comment` intact (decision (i)). Both
triggers fire only when `W == E`: from a drifted cwd the same command is still
blocked (FR6), because a relative `<dir>` resolved from the wrong cwd would run
the tail from the wrong place — the agent restores `E` first.

**FR5a — `cd <dir> && <rest>`.** A leading `cd` to a single directory argument
joined by `&&` to a non-empty tail, not satisfying FR4 (`_CD_AND`). The
`<dir>` argument may contain spaces when double-quoted, single-quoted, or
backslash-escaped, and may be followed by redirections before the `&&`
(`cd sub 2>&1 && <rest>`; decision (j)). Exclusions, all of which fall through
to the FR5 block: a bare `cd <dir>` with no `&&` tail; a pathless `cd && …`;
two-bareword `cd a b && …`; the `;` and `||` separators; and — when a worktree
is active — a `cd` to `$CLAUDE_PROJECT_DIR`, a cross-root transition governed
by `ExitWorktree` rather than a subdir descent. That last target is compared
de-quoted (`_cd_and_target`), so a quoted or spaced main-root path is excluded
too.

**FR5c — a `set -e` script with an embedded `cd`.** A command whose first
effective statement (after leading blank or `#`-comment lines) is a `set`
builtin that enables errexit — `set -e`, `set -eu`, `set -euo pipefail`,
`set -ex`, `set -o errexit` — and which the embedded-cd detector (FR5b) would
otherwise block. Exclusions, all of which fall through to the FR5b block: the
`+` forms (`set +e`, `set +o errexit`) and errexit-absent forms (`set -u`,
`set -o pipefail`, bare `set`); a `set` that is not the first statement
(`foo; set -e; cd x`); and `setup && …`. It fires only when an embedded `cd`
is present: a `set -e` script with no `cd` stays allow-silent under FR3,
untouched. Unlike FR5a it does **not** carve out a cross-root
`cd $CLAUDE_PROJECT_DIR` while a worktree is active — the embedded-cd detector
does not parse targets, and the restore returns cwd to the worktree regardless
(a transient command from main, not a persistent transition); a deliberate,
documented divergence (decision (l)).

**The announcement.** Both rewrites are announced on both channels, never
silent, and neither note echoes the command — Claude Code already surfaces the
rewritten `updatedInput`, so re-echoing is bloat. For FR5a, `additionalContext`
(`_REWRITE_AGENT_NOTE`) tells the agent its `cd` did not persist and that a
follow-up for that directory needs its own `cd <dir> && <command>`, an
absolute path, or `git -C`; `systemMessage` (`_REWRITE_USER_NOTE`) is a terse
"appended a cd back to root". For FR5c a dedicated agent note
(`_SET_E_AGENT_NOTE`) names `set -e`, states cwd was restored, and says that
`set -e` does not abort a Bash tool command; the user note is "appended a cd
back to root after set -e script."

---

## Design decisions

### (i) Rewrite `cd <subdir> && <cmd>` with a restore appended instead of blocking

**Decision:** At the effective root, a `cd <path> && <rest>` command is
rewritten in place to `cd <path> && <rest>`, newline, `cd <E>` via a
`PreToolUse` `updatedInput`, rather than blocked (FR5a). The restore is a flat
trailing line — never a `( … )` subshell, never joined with `;`. Bare
`cd <path>`, the `;`/`||` separators, a cross-root `cd` to `$CLAUDE_PROJECT_DIR`
while in a worktree, and the same command from a drifted cwd all remain blocked.
No message the hook emits advertises a `( … )` subshell form.

**Rationale:** Under decision (b) a `cd subdir && cmd` was blocked, and the
agent spent a turn reading the block and reissuing a cwd-safe form the hook
already knew it wanted. `PreToolUse` hooks can rewrite the tool input
(`hookSpecificOutput.updatedInput`), so the hook produces that form directly and
saves the turn. A transcript survey of the two months after the rewrite shipped
counted 591 rewrites in 127 sessions (median 2 per session, one session at 108)
against 86 leading-`cd` blocks, so the rewrite is the hook's main ergonomic path
and reverting it to a block would cost roughly a turn per rewrite. The
announcements cost a median 470 bytes per session — not a context tax.

The shape of the rewrite is dictated by the Bash tool's sandbox. Claude Code's
`sandbox.excludedCommands` matcher splits a command with a tree-sitter parse and
recurses only into `program`, `list` and `pipeline` nodes; a `( … )` subshell
(or `$( … )`, `sh -c '…'`, `if … fi`) is compared whole as one segment and
matches nothing. Verified live: `git log … && echo` runs unsandboxed under a
`git:*` exclusion, while the identical list inside `( … )` runs sandboxed. So a
subshell wrap silently downgrades a `cd sub && git status` that would have read
the real tree into one that reads the sandbox's masked view — exit 0, no error,
wrong answer. Flat lists (`a && b`, `a; b`, `a`⏎`b`, a heredoc followed by a
line) all keep the exclusion. The restore therefore goes on its own line: the
newline — not `;` — is what lets a trailing heredoc or `# comment` in the
agent's command survive (a `)` or `; cd E` appended after a heredoc's closing
delimiter breaks the command; the survey found 13 such manglings).

This is still **free on the security axis** in the sense that matters: the
appended `cd E` is the exact-match root path, shell-quoted, and the agent's
command is otherwise untouched. What changes is *how* cwd is kept: by a trailing
statement rather than by construction. A tail that `exec`s, `exit`s, or kills
the shell skips the restore and drifts — the same class of drift `pushd` causes
— and PostToolUse (FR7, decision (e)) is the backstop for it, exactly as before.
The survey found no such case.

The genuine cost is a different one: this is the first behavior that **mutates**
the agent's command rather than only allowing, blocking, or warning. Mutation
touches auditability (the executed command differs from what the transcript
shows the agent wrote) and least-surprise. That cost is paid down by making the
rewrite **never silent** — `additionalContext` (agent) and `systemMessage`
(user) both fire, so neither party is blind to the substitution and a follow-up
command that assumed persistence is corrected by context rather than by a
confusing wrong-cwd result. Neither note re-echoes the command: Claude Code
already surfaces the rewritten `updatedInput`, so the agent note instead states
cwd was restored and names the follow-up forms (its own `cd <dir> && <command>`,
an absolute path, `git -C`); the user note is a terse one-liner.

The rewrite is deliberately narrow. It fires only from `W == E` (from drift, the
only sanctioned command is the `cd E` restore; a relative `<dir>` from the wrong
cwd would run the tail from the wrong place). It requires a single directory
argument and an `&&` tail, so bare `cd subdir` — the persistent-drift *intent* —
still hits the FR5 block and still teaches. The directory may carry spaces when
quoted or backslash-escaped (a verbose `_CD_AND` regex matches one shell
argument: a "double"/'single'-quoted string or a bareword of plain chars and
`\`-escapes), so real-world paths like `cd "my dir" && …` are not needlessly
blocked; this is non-security-critical sugar — a wrong match merely appends a
restore or blocks (agent reissues), so the looser parsing here does not touch
the exact-match security matcher `_is_cd_to_root` (decision (d)), which is
untouched. It excludes `;`/`||` for the same reason FR4 does (decision (c)). And
while a worktree is active it excludes `cd $CLAUDE_PROJECT_DIR`: that is a
transition between the two managed roots, governed by `ExitWorktree` (decision
(h), "no single Bash call legitimately needs both roots"), not a subdir descent
— rewriting it would back-door exactly the cross-root operation decision (h)
forbids. The exclusion compares the *de-quoted* target to `$CLAUDE_PROJECT_DIR`,
so a quoted or escaped main-root path is caught just like a bare one.

### (l) Rewrite a `set -e` script with a restore appended, like `cd <dir> && <cmd>`

**Decision:** At the effective root, a command whose first statement enables
shell errexit (`set -e` / variant) and that contains an embedded `cd` — which
FR5b would otherwise block — is rewritten in place to `C`, newline, `cd <E>`
via a `PreToolUse` `updatedInput` (FR5c), the same treatment FR5a gives
`cd <dir> && <cmd>`. The `+` forms, errexit-absent `set`, a non-first `set`,
and the same script from a drifted cwd all remain blocked.

**Rationale:** The everyday shape

```
set -e
cd tools
make build
```

is the fail-fast-script idiom, and blocking it forced the agent to hand-rewrite
a multi-line script into a single `cd … && …` chain — the friction FR5a already
removed for the one-liner. FR5c removes it for the script.

`set -e` is the **trigger, not the guarantee**. Errexit is inert inside a Bash
tool command: Claude Code runs the command as
`bash -c "… && eval '<command>' && pwd -P >| …"`, where the `eval` is a
non-final element of an `&&` list, so by POSIX rule `-e` is ignored inside it —
and bash extends that to every compound command and subshell nested in it.
Verified live (CC 2.1.247): `set -e`⏎`cd /nonexistent`⏎`echo TAIL` prints `TAIL`
from the original cwd, inside a `( … )` wrap or not. So no rewrite can rest on
errexit stopping the tail after a failed `cd`; the tail runs from the original
cwd either way, exactly as it would in `cd x; make` — a correctness matter for
the agent's own script, not a cwd-safety matter. What FR5c guarantees is only
what FR5a guarantees: cwd is back at `E` when the call ends, by the appended
restore line. The `set -e`-first condition is kept as the trigger because it is
the agent's declared fail-fast intent, cheap to detect (first statement, modulo
leading blank/comment lines; a small regex anchored on `-` so `set +e` cannot
pass), and it keeps the rewrite off the general `;`-separated embedded-`cd`
shape, which stays a teaching block (FR5b). The agent note says outright that
`set -e` does not abort a Bash tool command, so the agent can chain with `&&`
where a step must not run after a failure.

The restore shape is flat for the reasons in decision (i): a `( … )` wrap hides
the script from the sandbox exclusion matcher and mangles a trailing heredoc.
As with FR5a's `_CD_AND`, a wrong match by the errexit matcher is not
security-critical — it can only append a restore or block (agent reissues) — so
it does not touch the exact-match matcher `_is_cd_to_root` (decision (d)).

Like FR5a this is a command **mutation**, so the same auditability cost applies
and is paid down the same way: the rewrite is never silent — a dedicated agent
note (naming `set -e`) and a terse user note both fire, so a follow-up that
assumed cwd persisted is corrected by context. And like FR5a it fires only from
`W == E` and only to *replace a block*: a `set -e` script with no `cd` is left
allow-silent (FR3), unmutated.

**Consciously diverged from FR5a:** the cross-root exclusion is *not* mirrored.
FR5a refuses to rewrite a leading `cd $CLAUDE_PROJECT_DIR` in a worktree
(decision (i), governed by `ExitWorktree`). FR5c's embedded-cd detector does not
parse the `cd` target — adding that parse would re-introduce exactly the
shell-parsing the project avoids — and the divergence is harmless: the restore
returns cwd to the worktree, so an embedded `cd <main>` runs transient commands
from main and cwd comes back. There is no persistent cross-root transition to
forbid.

---

## Rejected alternatives

For decision (i):

1. **Keep blocking and rely on the agent to reissue** — rejected, it wastes a
   turn for no safety gain now that `updatedInput` exists.
2. **Rewrite silently** — rejected on auditability/least-surprise grounds; the
   dual-channel announcement is mandatory.
3. **Also rewrite `cd <subdir>; <cmd>` / `|| <cmd>` and bare `cd <subdir>`** —
   rejected: `;`/`||` break the cd-first invariant the project rejects
   everywhere else, and a bare `cd` has no tail to scope, so a restore after it
   is a no-op that would suppress the teaching block.
4. **Rewrite from a drifted cwd too** — rejected: it would run the tail from
   the wrong directory; restore must come first.
5. **Wrap in a `( … )` subshell, so non-persistence holds by construction** —
   rejected: it defeats the sandbox exclusion matcher and mangles a trailing
   heredoc, both silently.
6. **Wrap as `(`⏎`…`⏎`)` to fix only the heredoc** — rejected: the exclusion
   downgrade remains.
7. **Drop the rewrite because of the sandbox interaction, and let cwd drift
   freely** — rejected: drift is what the plugin exists to prevent (decisions
   (a), (b)), and the survey shows the rewrite is the common path.

For decision (l):

1. **Require only that `set -e` precede the first `cd` (not be first)** —
   rejected: needs an offset scan for a case (`set -e` after non-cd setup)
   rare enough that falling through to the block is fine.
2. **Rewrite *every* `set -e` command, `cd` or not** — rejected: needless
   mutation and a notification for scripts FR3 already allows silently.
3. **Also honor `set -e` from a drifted cwd** — rejected: the tail would run
   from the wrong cwd; restore `E` first, as FR5a requires.
4. **Mirror FR5a's cross-root exclusion** — rejected: it needs embedded-target
   parsing for a case the restore already makes cwd-safe.
5. **Drop FR5c now that errexit is known to be inert** — rejected: the rewrite
   never depended on errexit for cwd safety (the restore does that), only its
   original rationale did, and the idiom is real (18 rewrites in the survey).
6. **Generalize the rewrite to every embedded-`cd` script, since errexit adds
   nothing** — rejected: `foo; cd x; make` with no declared intent stays a
   teaching block; the `set -e` trigger is the line between "the agent wrote a
   script" and "the agent drifted".
