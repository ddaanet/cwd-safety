## Remaining

- Run a retirement pass over `memory/MEMORY.md` to bring it under Claude Code's ~24.4 KB loader cap. It stands at 27619 bytes (111% of the 25600-byte advisory budget), so entries past the cutoff are silently dropped at load and never reach a session. Most of the overflow is inherited from upstream. Per the tier's own `index-compaction-triggers` rule the lever is retiring or merging whole entries, not shortening fact bodies (measured at ~2% with the index unmoved) and not under-triggering new lines.
