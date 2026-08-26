## Remaining

- Run a retirement pass on the memory index to bring it under the 24.4 KB loader cap, so the truncated tail becomes readable again.
- Process or discard `brief-sandbox-exclusions-relaxation.md`, the last unprocessed brief in the repo root.
- Decide whether to correct the tier's `cc-worktree-memory-freeze` claim that `ExitWorktree` restores `projectRoot`; it does not for a session that did not enter via `EnterWorktree`. This needs an upstream tier change rather than a local edit, since the tier is deliberately at `origin/live`.
- Fix the subshell rewrite mangling a trailing heredoc, per `heredoc-safety.txt`: wrap as `(` and a newline before `)` so heredoc syntax survives. Hit live this session.