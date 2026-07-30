# cwd-safety

A Claude Code plugin that keeps the agent's Bash working directory at
project root. The agent runs Bash from the project root or not at all —
no silent drift into a subdirectory, no commands that quietly report on
the wrong place.

## Why

Working-directory drift is a quiet failure mode: the agent runs `cd
subdir` (often a git submodule), and every later command runs from there.
A `git status` or `ls` from the wrong directory does not error — it
returns *plausible but wrong* information that the agent then treats as
authoritative. cwd-safety makes the project root a hard boundary so that
class of confusion can't start.

## How it works

A single Python hook (`scripts/cwd-safety.py`) fires on **`PreToolUse`**
and **`PostToolUse`** for the **Bash** tool. It enforces against the
**effective root `E`** — normally `$CLAUDE_PROJECT_DIR`, but the enclosing
git-worktree root when the command's cwd is inside a worktree of the
project (see *Worktrees* below). With effective root `E` and the command's
current working directory `W`:

| Situation | Decision |
|-----------|----------|
| At root (`W == E`), command isn't a `cd` | **allow** silently |
| `cd E` or `cd E && <cmd>` (root-anchored) | **allow** from anywhere |
| At root (`W == E`), `cd <subdir> && <cmd>` | **rewrite** to a subshell `(cd <subdir> && <cmd>)`, then allow |
| At root (`W == E`), a `set -e` script whose first statement enables errexit, with an embedded `cd` | **rewrite** the whole script to a subshell `(<script>)`, then allow |
| Any other leading `cd` (bare `cd subdir`, `cd ..`, `cd -`, `;`/`\|\|` tails) | **block** — even at root |
| Drifted (`W != E`), any other command | **block**, with the restore command |
| `PostToolUse` and drifted (`W != E`) | **warn** (agent + user channels) |

The pre-use side stops drift *before* it happens — a bare `cd <subdir>`
is the most common cause, so it's refused outright, even from root. The
ergonomic exceptions are both at root: a `cd <subdir> && <cmd>`, and a
`set -e` script with an embedded `cd` (errexit makes a failed `cd` abort
the script, the same cd-first guarantee `&&` gives), are each rewritten in
place to a non-persisting subshell and allowed (announced on both
channels), sparing the agent a block-then-reissue turn without letting cwd
move. The post-use side is a backstop that warns after drift the pre-use
gate can't intercept (e.g. `pushd`).

### Worktrees

When the agent works inside a git worktree of the project, that worktree's
root becomes the effective root `E` — the guard anchors there instead of
`$CLAUDE_PROJECT_DIR`, so a worktree is a first-class working directory
rather than drift. The worktree root is detected from the on-disk `.git`
linkage (read-only); there is no configuration. To *leave* a worktree, use
the `ExitWorktree` tool — not `cd`, which the guard blocks (including `cd
$CLAUDE_PROJECT_DIR`) to prevent silent drift out of the worktree.

### Running a command from somewhere other than root

- **Run from root in one line:** `cd /path/to/root && <command>` — the
  `&&` guarantees the command runs only if the `cd` succeeds.
- **Touch a subdirectory without drifting:** use a subshell,
  `(cd subdir && <command>)` — the directory change doesn't persist, so
  it's allowed. At root, a bare `cd subdir && <command>` is rewritten
  into this subshell form automatically.
- **Restore after drift:** `cd /path/to/root`.

Path matching is exact — the `cd` target must equal the effective root
literally (no normalization, no prefix matching). Only `&&` is accepted
after a root-anchored `cd`; `;` and `||` are not.

## Installation

From the `ddaanet` marketplace:

```
/plugin marketplace add ddaanet/claude-plugins
/plugin install cwd-safety@ddaanet
```

The hook activates wherever the plugin is enabled (resolved through
Claude Code's normal `enabledPlugins` scope chain). No per-repo files are
written — disabling the plugin removes the hook with it.

## Limitations

- `pushd`/`popd` aren't intercepted at PreToolUse (only `cd`); they're
  caught after the fact by the PostToolUse warning.
- A contrived `cd E && cd subdir && <cmd>` passes the pre-use gate and
  drifts; the PostToolUse warning is the backstop.
- One effective root at a time — either `$CLAUDE_PROJECT_DIR` or the
  enclosing worktree of it. Worktrees are recognized; unrelated repos and
  arbitrary multi-root setups are not.

See `docs/design.md` for the full requirements and decisions, and
`docs/changelog.md` for how they got that way.

## License

MIT
