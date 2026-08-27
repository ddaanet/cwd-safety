## Open decisions

- Keep `cd-usage-survey.md` (untracked, repo root: the transcript survey with its corrections and scripts) in the repo, move it under `plans/` or `docs/`, or drop it now that the changelog entry `docs/changelog/2026-08-27-restore-line-replaces-subshell.md` carries the numbers.

## Remaining

- Run a retirement pass on the memory index to bring it under the 24.4 KB loader cap (at 107%), so entries past the cutoff stop being silently dropped at load. Most of the overflow is inherited from upstream; merging or dropping entries is a judgement call deliberately kept out of a merge.
- Dogfood the restore rewrite live: issue a `cd docs && ls -a` from root and confirm cwd stays at root, the announcement reads right, and `$TMPDIR` is unset inside (exclusion preserved); then a trailing-heredoc `cd sub && cat <<'EOF'` case.
