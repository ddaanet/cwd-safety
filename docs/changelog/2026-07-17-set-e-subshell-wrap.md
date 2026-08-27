# 2026-07-17 — A `set -e` script with an embedded `cd` is wrapped in a subshell (v0.4.0)

The embedded-`cd` block introduced on
[2026-07-12](2026-07-12-redirect-tolerance-and-embedded-cd.md) also caught the
everyday fail-fast script idiom:

```sh
set -e
cd tools
make build
```

Blocking it forced the agent to hand-rewrite a multi-line script into a single
`(cd … && …)` chain — the same friction the
[subshell rewrite](2026-06-12-subshell-rewrite.md) had already removed for the
one-liner, reappearing in the shape where it hurts most.

At the effective root, a command whose **first statement enables errexit** and
which the embedded-`cd` detector would otherwise block is now rewritten to `(C)`
— the whole script scoped to a non-persisting subshell — instead of blocked.

## Why errexit licenses this

The subshell rewrite rests on `&&`: `cd <dir> && <cmd>` is safe to subshell
because `&&` guarantees the tail runs only if the `cd` succeeds, so a subshell
can never run the tail from the wrong cwd. `set -e` supplies
*the same guarantee by a different mechanism* — with errexit active, a failed
`cd` aborts the script before the tail runs. The `;`/newline separators refused
for the root-anchored form are neutralized here by errexit, precisely the way
`&&` neutralizes them. cwd non-persistence itself comes from the `( … )`,
identical to the `&&` case; errexit is what makes the *sequential* form safe to
wrap.

## The safety hinge, and the conservative condition chosen

Errexit must be active *before* the `cd`. The sufficient condition adopted is
"`set -e` is the **first** statement" (modulo leading blank or `#`-comment lines
— a shebang-like first line is a comment); then every later `cd` is protected.

This is stricter than "`set -e` appears somewhere before the first `cd`", and
deliberately so: it needs no position scan and no shell parse, and the failure
direction is safe — a non-first `set -e` simply falls through to the block and
the agent re-forms. Requiring only that `set -e` precede the first `cd` was
considered and rejected as an offset scan for a case rare enough that blocking
is fine.

The matcher is a small regex inspecting only the first statement's own options,
anchored on `-` so `set +e` cannot pass. It recognizes a `-`flag cluster
containing `e` (`set -e`, `set -eu`, `set -euo pipefail`, `set -ex`) and
`set -o errexit`; it uses a word boundary so `setup && …` is not mistaken for
`set`. Like the `cd <dir> && <cmd>` matcher it is not security-critical — a
wrong match can only subshell (still no-persist) or block — so it does not touch
the exact-match root matcher.

Excluded, all falling through to the block: `set +e` and `set +o errexit`,
errexit-absent forms (`set -u`, `set -o pipefail`, bare `set`), a `set` that is
not the first statement, and the same script from a drifted cwd (restore `E`
first).

## Scope and cost

It fires only to *replace a block*: a `set -e` script with no embedded `cd`
stays allow-silent and unmutated. Wrapping every `set -e` command regardless was
rejected — needless mutation, a notification for scripts already allowed
silently, and it would suppress the parent shell's own errexit as a side effect.

Like the earlier rewrite this is a command **mutation**, so the same
auditability cost applies and is paid the same way: never silent. A dedicated
agent note names `set -e` and states cwd did not persist; the user note is a
terse "wrapped set -e script in a subshell". Neither echoes the command.

## Consciously diverged from the `cd <dir> && <cmd>` rewrite

The cross-root exclusion is **not** mirrored. That rewrite refuses to subshell a
leading `cd $CLAUDE_PROJECT_DIR` while a worktree is active. The embedded-`cd`
detector does not parse the `cd` target, and adding that parse would
re-introduce exactly the shell-parsing the project avoids. The divergence is
harmless: the wrap is a non-persisting subshell, so an embedded `cd <main>` runs
one transient command from main and cwd returns to the worktree. There is no
persistent cross-root transition to forbid.

See "Rewrite a `set -e` script to a subshell" in [design.md](../design.md).
