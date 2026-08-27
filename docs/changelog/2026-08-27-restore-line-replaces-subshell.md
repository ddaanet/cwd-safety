# 2026-08-27 — The rewrite appends a `cd` back to root instead of wrapping in a subshell

The [subshell rewrite](2026-06-12-subshell-rewrite.md) and the
[`set -e` wrap](2026-07-17-set-e-subshell-wrap.md) both produced `( … )`. Both
now produce the command as written, a newline, and `cd <E>` (shell-quoted):

```sh
cd tools && make build
cd /path/to/root
```

Decisions (i) and (l) are rewritten in place; FR5a, FR5b, FR5c and the
limitations list follow. Three findings drove it, in order of weight.

## `( … )` silently defeats the sandbox exclusion matcher

A brief from the gitlore repo, written after decompiling Claude Code's
`sandbox.excludedCommands` matcher, reported that the matcher parses a command
with tree-sitter and recurses only into `program`, `list` and `pipeline` nodes;
a `( … )` subshell, `$( … )`, `sh -c '…'` or `if … fi` is compared whole as one
segment and matches nothing. Verified live on CC 2.1.247 with a `git:*`
exclusion: `git log … && echo "$TMPDIR"` runs unsandboxed (`TMPDIR` unset), the
identical list inside `( … )` runs sandboxed (`TMPDIR=/tmp/claude-1000`). Flat
lists — `&&`, `;`, newline, a heredoc followed by a line — all keep the
exclusion.

So the rewrite was converting a `cd sub && git status` that would have read the
real working tree into one that read the sandbox's masked view, with exit 0 and
no error. The brief measured a sandboxed `git status` reporting 19 untracked
paths where the truth was 3.

The brief also proposed dropping the leading-`cd` block and the embedded-`cd`
block outright, on the argument that `cd X && cmd` is safe wherever it is
issued. That part is not taken: the sandbox evidence indicts the `( … )` wrap,
not the block, and my human partner withdrew the safety argument — from a
non-root cwd a relative `cd X` means something other than the agent intended.

## A trailing heredoc was mangled by the closing `)`

Hit live while driving this repo's own hook: `cd sub && cat <<'EOF'`⏎`…`⏎`EOF`
became `(cd sub && cat <<'EOF'`⏎`…`⏎`EOF)`, where `EOF)` is not the delimiter.
A newline before the restore fixes it; a `;` would not.

## `set -e` is inert inside a Bash tool command

Probing whether the Bash tool still persists cwd (it does — the wrapper is
`bash -c "source <snapshot> || true && … && eval '<command>' && pwd -P >| /tmp/claude-<uid>/cwd-<id>"`,
and the trailing `pwd -P` is the capture) showed that `set -e` never aborts:
the `eval` is a non-final `&&` element, so errexit is ignored inside it, and
bash extends that to nested subshells. The FR5c rewrite
`(set -e`⏎`cd /nonexistent`⏎`echo TAIL)` printed `TAIL` from root.

Decision (l) rested on errexit giving the cd-first guarantee `&&` gives. It
does not. The rewrite is kept — cwd safety was always provided by the wrap,
now by the restore line, never by errexit — with `set -e` demoted to the
trigger (the agent's declared fail-fast intent), and the agent note now says
that `set -e` does not abort a Bash tool command.

## What the transcript corpus said

A survey of every session transcript on this machine since the plugin's first
release (12,777 Bash calls, 468 sessions; Sonnet-drafted, then its
rewrite-derived numbers were re-derived by hand after the first pass had
counted other hooks' `updatedInput`s):

- cwd-safety rewrites: 573 `cd <dir> && …` + 18 `set -e` = 591, in 127
  sessions; median 2 per session, one session at 108. Announcement bytes:
  median 470 per session, max 25 KB. The "context bloat" worry behind the
  brief's relaxation is not borne out at the median.
- Leading-`cd` calls: 1,203, of which 951 target an absolute non-root path
  (mostly sibling repos), 122 a relative path, 36 the root. `git -C` appears in
  1,252 calls.
- Leading `cd` issued from a drifted cwd: **0**. Drift blocks overall: 16, none
  a `cd`. The relaxation's target case does not occur.
- Heredocs: 27 rewritten commands contained one; 13 ended in a trailing
  heredoc and were mangled.

## Also recorded

FR5b's embedded-`cd` regex blocks a hand-written `(set -e; cd x; …)` and any
heredoc body containing `; cd` — hit twice while editing this change. Logged as
an accepted limitation, not fixed: it only blocks, and the recommended form is
no longer a subshell anyway.
