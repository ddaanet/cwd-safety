## Remaining

- Run a retirement pass over `memory/MEMORY.md`. After the tier merge the hook reports it at 95% of the 25600-byte advisory budget, so there is almost no headroom before entries past the ~24.4 KB loader cap are silently dropped at load. Per the tier's `index-compaction-triggers` rule the lever is retiring or merging whole entries, not shortening fact bodies.
- Memory changes made after the last memory commit are on disk but not on the remote. They publish with an approved summary and a memory commit riding the next parent commit, then `/gitlore:push`.