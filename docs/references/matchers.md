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
  top-level separator or compound-body keyword is blocked, matched over a
  quote/comment/heredoc/paren-masked copy of the command

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

**`_EMBEDDED_CD` over `_mask_opaque` — FR5b.**
`(?:&&|\|\||;|&|\n|\{|(?<!\S)(?:then|do)(?=\s))\s*cd(?:\s|;|&|$)`: a `cd`
immediately after a top-level sequencing operator, a newline, or a
compound-body opener (`then`, `do` as whole words; `{`). It is searched in
`_mask_opaque(command)`, never the raw command. The mask is a single
left-to-right scan that replaces, character for character, everything that
cannot hold a current-shell `cd` with spaces (newlines are kept, so the
separator set is unchanged): `'…'`, `"…"` (with `\"` escapes) and `` `…` ``
strings; a `\`-escaped character; a word-initial `#` comment to end of line
(`$#` and `a#b` are not comments); a heredoc body — `_HEREDOC` recognises
`<<`/`<<-` with a bare or quoted delimiter word, the body runs from the next
newline to the line equal to the delimiter, leading tabs stripped for `<<-`,
several heredocs on one line consumed in order. The delimiter word takes `-`
and `.` after its first character (`<<'END-OF'`, `<<EOF.txt`), but that first
character stays `[A-Za-z_]`, so a `$((1 << 3))` left shift cannot read as a
heredoc opener and blank the rest of a multi-line command; a digit-initial or
space-containing delimiter is the residual, and costs only a spurious block.
Also blanked: every character inside
parentheses at any depth, which covers a `( … )` subshell, `$( … )` and
`<( … )` alike. Depth is clamped at zero, so a `case` pattern's lone `)` cannot
blank the rest of the command, and `$(( … ))` arithmetic is simply two nested
regions. An unterminated quote blanks to the end of the command.

So `mkdir x && (cd x && ls)`, `(set -e; cd x; make)`, `echo "x && cd y"`,
`python3 - <<'EOF' … EOF` with a body that mentions `; cd`, and `ls # && cd`
are all allowed, while `cat <<EOF && cd sub`, `cat <<EOF … EOF` followed by
`cd sub` on the next line, `if …; then cd sub; fi`, `true && { cd sub; }` and
`x=$((1+2)); cd sub` are blocked. A `foo | cd sub` pipeline is never caught
(single `|` is not a separator; its `cd` runs in a subshell). What the scanner
does not model — `eval`, `builtin cd`, `command cd`, `\cd`, a `cd` in a
function body counted at definition rather than at call — only ever costs a
missed block or a spurious one. The scanner feeds FR5b alone and never the
root anchor.

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
embedded `cd` running in the current shell right after a top-level separator or
a compound-body keyword (FR5b) is blocked, even though `cd` is not the command's
leading token. (3) The embedded-`cd` match runs over a masked copy of the
command — quotes, comments, heredoc bodies and parenthesised regions blanked —
produced by a stdlib scanner, not by a shell parser.

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
immediately follow the separator excludes pipe subshells for free, and
`then`/`do`/`{` are in the separator set because a `cd` in a conditional, loop
or group body runs in the current shell exactly as a `&& cd` does. The
residual false positives (a no-op `echo x && cd E`; a function body that `cd`s,
counted at definition) only ever *block* — the agent re-forms the command — so
they cost ergonomics, never safety. This is a targeted retreat from the "don't
chase an exhaustive blocklist" non-goal, not an abandonment of it: PostToolUse
remains the backstop for `pushd`, `source`, `&& cd` inside a root-anchored FR4
command, and everything the regex cannot see. The contrived leading form
`cd E && cd sub && …` is *not* covered — it satisfies FR4 and is allowed by
design.

The mask exists because the raw regex blocked on text that merely *mentions* a
`cd`: a quoted literal, a `#` comment, a heredoc body (a script fed to
`python3 - <<'EOF'` that contains `; cd` — hit in practice, and the agent's only
way out was to move the script into a file), and a `cd` that is not the first
statement of a subshell (`(set -e; cd x; …)`). Each of those regions is
delimited by tokens a left-to-right scan can pair without a grammar — quote to
quote, `<<WORD` to the `WORD` line, `(` to `)` — so blanking them costs one
pass over the command and nothing outside the stdlib. The scanner is
deliberately not asked to understand what it blanks: it never decides *which*
`cd` is the drift, only which characters cannot be one. Its own errors are
bounded the same way the regex's are — a misjudged region either hides a real
`cd` (a missed block, PostToolUse catches the drift) or exposes text that
looks like one (a spurious block, the agent re-forms) — and it feeds FR5b
alone, so decisions (c) and (d) are untouched by it.

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

**Parse the command with tree-sitter-bash instead of masking** (j). Rejected on
deployment, not on quality. Probed against 32 commands covering every known
false positive and the compound-body shapes, `tree-sitter-bash` 0.25.1 was
right on all of them, and loading the grammar costs about 50 ms. But both
`tree-sitter` and the grammar are C extensions (about 3.5 MB installed), the
hook runs under whatever `python3` Claude Code's environment resolves — the
system interpreter in one shell, a project venv under direnv in another — and a
plugin has no install step. An optional import with a regex fallback makes the
guard's behaviour depend on which `python3` wins `PATH` on each machine and is
silently the fallback for every other user of the plugin; vendoring wheels
means per-platform, per-ABI binaries in the plugin repository for a hook of a
few hundred lines. It would also break NFR3 outright. The pure-Python `bashlex`
fails the same test on its own terms: GPLv3 against the plugin's MIT, and it
rejects `<<'EOF'` heredocs and `$( … )` — the very cases at issue. The scanner
reproduces the parser's verdict on every probed case with no dependency, and
because FR5b's failure modes are bounded either way, the parser's extra
precision buys nothing the guard can use.
