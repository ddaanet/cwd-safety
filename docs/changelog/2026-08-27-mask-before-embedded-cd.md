# 2026-08-27 — FR5b matches over a masked command; tree-sitter evaluated and rejected

The embedded-`cd` regex now runs over `_mask_opaque(command)` — quoted strings,
`#` comments, heredoc bodies and every parenthesised region blanked to spaces —
and its separator set gains `then`, `do` and `{`. Decision (j) gains a third
clause and a rejected alternative; FR5b and the matching limitation are
rewritten.

## What moved

Three documented false positives blocked commands that could not drift: a
quoted literal mentioning `&& cd`, a heredoc body mentioning `; cd` (a
`python3 - <<'EOF'` script — hit live in the session that opened this work,
and again while writing its tests), and a `cd` that is not the first statement
of a subshell (`(set -e; cd x; …)`). Each cost the agent a turn and usually a
detour through the Write tool. A `#` comment mentioning `&& cd` was a fourth,
found while probing.

The question on the table was whether to replace the regexes with a
tree-sitter bash parse. It was probed rather than argued: 32 commands through
`tree-sitter-bash` 0.25.1, then the same 32 through a 60-line stdlib scanner
that blanks the regions a `cd` cannot live in. The parser was right on every
case, at ~50 ms to load. The scanner agreed with it on every false-positive row
and kept every true positive the regex had. The only behavioural difference is
an unterminated quote, which now falls through to PostToolUse instead of
blocking — bash refuses that command regardless.

## Why the scanner and not the parser

Deployment. The hook runs under whatever `python3` Claude Code's environment
resolves, a plugin has no install step, and both tree-sitter packages are C
extensions. An optional import with a fallback would make the guard behave
differently per machine and be silently the fallback for every other user;
vendoring wheels puts per-platform binaries in the plugin. `bashlex`, the
pure-Python option, is GPLv3 against MIT and cannot parse the heredoc and
`$( … )` cases that motivated the change. Full argument in
[matchers.md](../references/matchers.md), rejected alternatives.

## Why `then`/`do`/`{` joined the separators

Widening was a separate call, taken at the same time: a `cd` in an `if`, `for`
or `{ … }` body runs in the current shell exactly as a `&& cd` does, and the
parser probe showed the regex missing all three. The cost is one shape that
blocks without drifting — a function definition whose body `cd`s, counted at
definition rather than at call — and it is documented as such.
