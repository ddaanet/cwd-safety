# cwd-safety Changelog

How the design got to its current shape. Each entry is a write-time record of
one change: what moved, and the reasoning available at the time. Entries are
never revised — a dated record is correct forever precisely because it is dated.
The living design is [design.md](design.md); when a decision there is
overturned, it is rewritten in place and the reversal gets an entry here.

Newest first.

- [2026-08-27 — FR5b matches over a masked command](changelog/2026-08-27-mask-before-embedded-cd.md)
  — quoted, commented, heredoc and parenthesised `cd`s no longer block;
  `then`/`do`/`{` join the separators; tree-sitter probed and rejected on
  deployment.
- [2026-08-27 — Restore line replaces the subshell](changelog/2026-08-27-restore-line-replaces-subshell.md)
  — `( … )` defeats the sandbox exclusion matcher and mangles a trailing
  heredoc, and `set -e` is inert under the Bash tool; the rewrite now appends a
  newline and `cd <E>`
- [2026-07-17 — `set -e` subshell wrap](changelog/2026-07-17-set-e-subshell-wrap.md)
  — errexit gives the same cd-first guarantee `&&` does, so a fail-fast script
  is as safe to wrap as the one-liner (v0.4.0)
- [2026-07-14 — Fail open on a deleted root](changelog/2026-07-14-fail-open-deleted-root.md)
  — once `E` is gone the guard's contract is unsatisfiable, so it steps aside;
  the shape-2 self-destruct guard is dropped (v0.3.1)
- [2026-07-12 — Redirect tolerance and the embedded-`cd` block](changelog/2026-07-12-redirect-tolerance-and-embedded-cd.md)
  — three defects surfaced by a background worktree session that deleted its own
  cwd (v0.3.1)
- [2026-06-12 — Subshell rewrite](changelog/2026-06-12-subshell-rewrite.md) —
  `cd <subdir> && <cmd>` is rewritten rather than blocked; the first behavior
  that mutates a command (v0.3.0)
- [2026-06-07 — Worktrees become the effective root](changelog/2026-06-07-worktree-effective-root.md)
  — a single effective root `E`, detected from the on-disk `.git` linkage
  because no payload field exists (v0.2.0)
- [2026-06-04 — Extracted from agent-core](changelog/2026-06-04-extraction-from-agent-core.md)
  — five months as `submodule-safety.py`, then a rename, the proactive
  leading-`cd` block, and plugin packaging (v0.1.0)
