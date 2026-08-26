## Current task

The shared ddaanet conventions import is complete and verified: `CLAUDE.md` now ends with `@memory/ddaanet/shared-claude.md`, a fresh session confirmed the import resolves across both the submodule and tier boundary, and the superseded vendored-subtree rule was dropped from `CLAUDE.md` in favour of the shared file's version (which also corrects the stale `just update-plugin-dev vX.Y.Z` form to `dist-vX.Y.Z`). The `ddaanet` tier sits exactly at `origin/live` with zero local commits, so it has nothing to publish. The project memory store holds the demoted `cc-worktree-cwd-shapes` fact plus two lessons written this session.

## Open decisions

- The memory index is ~1.5 KB over its 24.4 KB loader cap, so entries past the cutoff are silently dropped at load. Most of the overflow is inherited from upstream, and a retirement pass was deliberately not attempted inside the merge. Which entries to merge or drop is unmade.
- `markdown-formatter-choice.md` has a line in the tier's own index but none in the root index, so it is not recallable here. Either add a `ddaanet/`-prefixed root line, or treat it as upstream drift to fix at the source rather than locally.
- The gitlore memory gate has an unapproved summary: the proposed commit message describes demoting `cc-worktree-cwd-shapes` to the project store and unwinding the tier to `origin/live`, and has not been confirmed.