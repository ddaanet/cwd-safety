# The command matchers

What each regex in `scripts/cwd-safety.py` accepts, which of them are
security-critical, and the decisions arguing for the `&&`-only and exact-match
invariants. FR4, FR5 and FR5b conclude in [design.md](../design.md); this file
is what you need while changing a matcher or reading a block you did not
expect. The rewrite the looser matchers feed is in
[restore-rewrite.md](restore-rewrite.md); the effective root they anchor on is
in [worktrees.md](worktrees.md).

- Invariants — **(c)** only `&&` is accepted after the root-anchored `cd`; `;`
  and `||` never qualify · **(d)** the root is matched literally, with no
  normalization, traversal or prefix match · **(j)** redirections are tolerated
  between a `cd` target and its `&&`, and an embedded `cd` right after a
  top-level separator is blocked

---

## The matchers

**`_is_cd_to_root` — FR4, security-critical.** Anchors on the effective root
`E` as an exact `re.escape`d string in the forms `cd E`, `cd "E"`, `cd 'E'`,
each optionally followed by redirections (`_REDIRS`: `cd E 2>&1 && <rest>`,
`cd E >log && <rest>`, `cd E 2>/dev/null`) and then either the end of the
command or `&& <rest>`. `&&` is the only separator; `;` and `||` block
(`cd E 2>&1; <rest>` blocks too).
Never loosened: the matchers below that feed the restore rewrite are
deliberately looser, and none of them feeds this one.

**`_LEADING_CD` — FR5.** `^cd(?:\s|;|&|$)`: the unquoted `cd` builtin as the
first token, so `cd`, `cd …`, `cd;…` and `cd&&…` match and `cdfoo` does not. A
quoted `'cd'` or `\cd` is not matched (see Limitations in the hub).

**`_CD_AND` — FR5a's argument grammar.** A verbose regex matching a leading
`cd`, one shell argument — a "double"-quoted or 'single'-quoted string, or a
bareword of plain characters and `\`-escapes — optional redirections, then
`&&` and a non-empty tail. So `cd "my dir" && …` and `cd sub 2>&1 && …` qualify
and `cd a b && …`, a pathless `cd && …` and a bare `cd sub` do not.
`_cd_and_target` de-quotes the argument so the worktree cross-root exclusion
(FR5a) compares the real path.

**`_REDIR` / `_REDIRS` — the redirect grammar, decision (j).** An fd-dup
(`2>&1`, `>&2`, no filename), `&>file`, or `[n]>`/`>>`/`<` followed by a
filename token that excludes `& | ; < > ( )`. Bounded so it can never swallow
the `&&` or introduce a second command.

**`_EMBEDDED_CD` — FR5b.** `(?:&&|\|\||;|&|\n)\s*cd(?:\s|;|&|$)`: a `cd`
immediately after a top-level sequencing operator or newline. A `(cd sub && …)`
subshell and a `foo | cd sub` pipeline (single `|`, its `cd` runs in a subshell)
are never caught, because the `cd` there does not follow one of those
separators. It is a drift detector, not a shell parser: a quoted literal or
heredoc body containing `&& cd` / `; cd`, and a `cd` that is not the first
statement of a subshell (`(set -e; cd x; …)`), are known false positives that
only ever block.

**`_SET_ERREXIT` with `_LEADING_SKIP` — FR5c's trigger.** After skipping
leading blank and `#`-comment lines, `_starts_with_errexit` requires the first
statement to be a `set` builtin (matched with `\b`, so `setup && …` is not
`set`) whose flags enable errexit: a `-`flag cluster containing `e` (`set -e`,
`set -eu`, `set -euo pipefail`, `set -ex`) or `set -o errexit`. The `+` forms
and errexit-absent forms (`set -u`, `set -o pipefail`, bare `set`) do not
match.

The three looser matchers — `_CD_AND`, `_EMBEDDED_CD`, `_SET_ERREXIT` — are
not security-critical: a wrong match only appends a restore or blocks, and the
agent reissues. They must never feed the root anchor.

---

## Design decisions

### (c) Only `&&` accepted in the root-anchored form

**Decision:** `cd E && cmd` is allowed; `cd E; cmd` and `cd E || cmd` are not.

**Rationale:** `&&` makes `cmd` conditional on the success of `cd E`. If the
`cd` fails — e.g., because `$CLAUDE_PROJECT_DIR` is wrong — `cmd` does not
run. `;` provides no such guarantee: `cmd` runs even if `cd E` fails, meaning
the hook could be spoofed by constructing a `cd <path>` that matches the regex
but points to a wrong location. `||` has the opposite semantics (run `cmd` if
`cd` fails), which makes no sense as a safety form. The choice is stated
explicitly in `scripts/cwd-safety.py`'s module docstring, alongside the
`_is_cd_to_root` regex that enforces it.

### (d) Exact path match, no traversal or normalization

**Decision:** The hook matches the effective root literally using
`re.escape`. No `os.path.normpath`, no `os.path.realpath`, no prefix matching.

**Rationale:** Normalization opens substitution attacks (a path that normalizes
to `E` but is not literally `E`). Prefix matching allows `cd /project-root-
extra/` to pass as matching `/project-root/`. The exact match is conservative:
if `$CLAUDE_PROJECT_DIR` contains a trailing slash or uses symlinks, it will
not match a command that does not. This is a known sharp edge — the user must
use the exact string that `$CLAUDE_PROJECT_DIR` expands to. The conservative
choice is correct because the hook's job is security, not ergonomics. Worktree
detection (decision (h)) reads the filesystem, but only to *find* `E`; the
`cd E` match itself stays exact.

### (j) Redirect tolerance and the narrow embedded-cd block

**Decision:** (1) The root-anchored allow (FR4) and the restore rewrite (FR5a)
tolerate shell redirections between the `cd` target and the `&&`. (2) An
embedded `cd` running in the current shell right after a top-level separator
(FR5b) is blocked, even though `cd` is not the command's leading token.

**Rationale:** Both address natural command forms the matchers mishandled.
`cd <dir> 2>&1 && <cmd>` — capturing the `cd`'s own diagnostics — was blocked
because a redirection sat between the path and the `&&`; yet a redirection
cannot change the cd-first guarantee `&&` provides (the tail still runs only if
`cd` succeeds), so tolerating it is free on the security axis. The redirection
grammar is deliberately bounded — an fd-dup (`2>&1`, no filename) or a filename
token that excludes `& | ; < > ( )` — so it can never swallow the `&&` or
introduce a second command; a non-`&&` separator (`cd E 2>&1; <cmd>`) still
blocks (decision (c) holds). The exact path match (decision (d)) is untouched:
the redirections sit *after* the exactly-matched path.

The embedded-cd block narrows the "contrived `cd E && cd subdir` passes" gap for
its most common real shape, `<setup> && cd <subdir> && <work>`, which drifts
from root and was previously only caught after the fact by PostToolUse. It is a
deliberately *narrow* regex, not a shell parser: requiring the `cd` to
immediately follow the separator excludes a hand-written `(cd sub && …)`
subshell and pipe subshells for free, and the residual false positives (a quoted
`"&& cd"` literal; a no-op `echo x && cd E`) only ever *block* — the agent
re-forms the command — so they cost ergonomics, never safety. This is a targeted
retreat from the "don't chase an exhaustive blocklist" non-goal, not an
abandonment of it: PostToolUse remains the backstop for `pushd`, `source`,
`&& cd` inside a root-anchored FR4 command, and everything the regex cannot see.
The contrived leading form `cd E && cd sub && …` is *not* covered — it satisfies
FR4 and is allowed by design.

---

## Rejected alternatives

**Accept `;` for ergonomics** (c). Rejected: breaks the security invariant; the
agent can always use `&&` instead.

**Normalize both sides before matching** (d). Rejected on anti-confabulation
grounds: the failure modes of normalization in edge cases (symlinks, bind
mounts, case-sensitive filesystems) are non-trivial, and there is no evidence
the exact match causes practical problems.

**Allow arbitrary tokens (not just redirections) between the target and `&&`**
(j). Rejected: junk like `cd E ; rm && cmd` would break the cd-first
invariant.

**Rewrite the embedded `cd` into a subshell rather than block** (j). Rejected:
correctly splitting a chain around quotes and nested subshells needs a real
parser (chosen over in brainstorming).

**Leave embedded `cd` to PostToolUse entirely** (j). Rejected: the drift
executes at least one command from the wrong cwd before the warning fires, the
failure mode the block exists to prevent.
