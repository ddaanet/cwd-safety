## Remaining

- Run a retirement pass on the memory index to bring it under the 24.4 KB loader cap, so entries past the cutoff stop being silently dropped at load. Most of the overflow is inherited from upstream, and merging or dropping entries is a judgement call deliberately kept out of a merge.
- Process or discard `brief-sandbox-exclusions-relaxation.md`, the last unprocessed brief in the repo root.
- Decide whether to correct the tier's `cc-worktree-memory-freeze` claim that `ExitWorktree` restores `projectRoot`; it does not for a session that did not enter via `EnterWorktree`. The qualification lives in the project-local `cc-worktree-cwd-shapes`, so the tier still overstates it.
- Fix the subshell rewrite mangling a trailing heredoc, per `heredoc-safety.txt`: wrap as `(` and a newline before `)` so heredoc syntax survives. Hit live while driving this repo's own hook.