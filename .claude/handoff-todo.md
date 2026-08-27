## Open decisions

- Whether to adopt tree-sitter (bash grammar) for command parsing. It would remove the FR5b false positives (heredoc/quoted `&& cd`, non-first `cd` in a subshell) and let `_CD_AND`/`_SET_ERREXIT` parse real statements, but it breaks NFR3 (stdlib only, no third-party packages) and the hook runs under whatever `python3` Claude Code finds — a missing binding must fail open or fall back to the regexes. Decision (j) already rejected a subshell-rewrite on the grounds that splitting a chain needs a real parser; a parser reopens that too. Read `docs/references/matchers.md` before deciding.

## Remaining

- Run a retirement pass on the memory index to bring it under the 24.4 KB loader cap (at ~108%), so entries past the cutoff stop being silently dropped at load. Most of the overflow is inherited from upstream; merging or dropping entries is a judgement call deliberately kept out of a merge.
- Dogfood the restore rewrite live: issue a `cd docs && ls -a` from root and confirm cwd stays at root, the announcement reads right, and `$TMPDIR` is unset inside (exclusion preserved); then a trailing-heredoc `cd sub && cat <<'EOF'` case.
