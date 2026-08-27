## Current task

The FR5a/FR5c rewrite now appends a newline and `cd <E>` instead of wrapping in `( … )` (sandbox `excludedCommands` matcher and trailing heredocs; `set -e` found inert under the Bash tool, decision (l) rewritten). Landed with design.md, changelog entry and tests; the memory qualification of `cc-worktree-memory-freeze` is written with an approved summary waiting for the amend. Next is the memory-index retirement pass and dogfooding the new rewrite live.
