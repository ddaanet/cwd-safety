## Current task

The fail-open-on-deleted-root feature and shape-2 self-destruct-guard removal
(spec `2026-07-13-failopen-deleted-root-design.md`) are implemented, tested, and
`just precommit`-green; the only queued follow-up is consuming `note-release.md`.

## Open decisions

- What to do with `note-release.md` (deferred, "consumed later"): it documents
  that the `release` recipe dropped its interactive confirmation prompt and
  `--yes` arg — already effected upstream by plugin-dev v0.3.0 (commit
  `aa3c8a3`), so consuming it likely means folding the fact into memory/docs or
  deleting the file rather than editing `release.just` by hand.
