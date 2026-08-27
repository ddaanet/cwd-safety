## Remaining

- Run a retirement pass on the memory index to bring it under the 24.4 KB loader cap (at ~108%), so entries past the cutoff stop being silently dropped at load. Most of the overflow is inherited from upstream; merging or dropping entries is a judgement call deliberately kept out of a merge.
