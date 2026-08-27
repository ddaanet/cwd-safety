## Remaining

- Run a retirement pass on the memory index to bring `memory/MEMORY.md` under the 24.4 KB loader cap (currently ~109%), so entries past the cutoff stop being silently dropped at load. Most of the overflow is inherited from upstream; merging or dropping entries is a judgement call deliberately kept out of a merge.
