# cwd-safety worktree support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the `cwd-safety` hook treat an active git worktree as the
working-directory anchor, instead of blocking every command issued from it.

**Architecture:** A single *effective root*
`E = (enclosing git-worktree root, if cwd is inside a worktree of $CLAUDE_PROJECT_DIR) else $CLAUDE_PROJECT_DIR`,
computed in `main()` and threaded into both handlers in place of the project
dir. **Detection is filesystem-based** (read the `.git` linkage) — there is no
payload field (verified, see Task 1). When cwd is not in a worktree of the
project, behavior is byte-for-byte identical to today.

**Tech Stack:** Python 3 stdlib only (`json`, `os`, `re`, `sys`). Tests: stdlib
subprocess driver + real temp `.git` fixtures (no pytest). Gate:
`just precommit`.

**Spec:** `plans/2026-06-07-cwd-safety-worktree-design.md`

---

## Task 1 — Verify the worktree signal (DONE)

Ran the empirical gate: captured a real PreToolUse payload from inside an
`isolation: "worktree"` subagent via a temporary capture hook.
**Finding: there is NO `worktree` field** in the payload (the earlier
web-sourced claim was confabulated). Detection must come from the filesystem.
All probe artifacts removed. This finding drives Tasks 3+.

## Task 2 — Effective-root plumbing (DONE, commit `71b02e1`)

Introduced `effective_root`/`in_worktree`, threaded them through
`handle_pretooluse`/`handle_posttooluse`, renamed `_is_cd_to_root`'s param to
`root`, and extracted three message helpers (`_cd_block_message`,
`_drift_block_message`, `_drift_warn_message`) carrying `in_worktree`.
**Superseded parts:** the detection line
`worktree = hook_input.get("worktree") or ""` (Task 3 replaces it) and the
field-injection test cases (Task 3 rewrites them). The plumbing itself is
correct and reused.

---

### Task 3: Filesystem worktree detection

Replace the payload-field detection with a filesystem git-worktree check, and
rewrite the worktree tests to use real on-disk fixtures.

**Files:**
- Modify: `scripts/cwd-safety.py`
- Modify: `tests/test_cwd_safety.py`

- [ ] **Step 1: Rewrite the worktree tests to use real fixtures (failing
      first)**

In `tests/test_cwd_safety.py`:

(a) Add `tempfile`, `atexit`, `shutil` to the imports (they currently are
`json, os, subprocess, sys`):

```python
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
```

(b) Replace the constants block (current lines ~16-23, from `ROOT = ...` through
the `HOOK = ...` line, including the `WT`/`WTSUB`/`_UNSET` lines added in Task
2) with:

```python
ROOT = "/project/root"  # pretend $CLAUDE_PROJECT_DIR for the non-worktree cases
SUB = "/project/root/subdir"

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "cwd-safety.py")

# ── Real on-disk fixtures for filesystem worktree detection ─────────────────
# Detection reads the `.git` linkage, so worktree cases need actual dirs/files.
_TMP = tempfile.mkdtemp(prefix="cwdsafety-test-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

PROJ = os.path.join(_TMP, "proj")        # a real project root
WT = os.path.join(_TMP, "wt1")           # worktree root OUTSIDE proj (location-independent)
WTSUB = os.path.join(WT, "src")          # a subdir inside the worktree
PROJSUB = os.path.join(PROJ, "subdir")   # ordinary subdir of the main tree
OTHER = os.path.join(_TMP, "other")      # foreign repo: .git is a directory
EVIL = os.path.join(_TMP, "evil")        # spoof: .git file whose gitdir is outside PROJ

os.makedirs(os.path.join(PROJ, ".git", "worktrees", "wt1"))
os.makedirs(PROJSUB)
os.makedirs(WTSUB)
os.makedirs(os.path.join(OTHER, ".git"))
os.makedirs(EVIL)
with open(os.path.join(WT, ".git"), "w") as _f:
    _f.write("gitdir: " + os.path.join(PROJ, ".git", "worktrees", "wt1") + "\n")
with open(os.path.join(EVIL, ".git"), "w") as _f:
    _f.write("gitdir: " + os.path.join(_TMP, "elsewhere", ".git", "worktrees", "x") + "\n")
```

(c) Replace the `run`/`allowed`/`blocked`/`blocked_with` definitions (Task 2
gave them a `worktree=_UNSET` param) so they take `root=ROOT` instead:

```python
def run(event, cwd, command="", root=ROOT):
    """Invoke the hook; return (exit_code, stdout, stderr).

    root sets $CLAUDE_PROJECT_DIR for the call (worktree fixtures pass PROJ).
    """
    payload = {"hook_event_name": event, "cwd": cwd, "tool_input": {"command": command}}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check(label, cond):
    global _fails
    if cond:
        print(f"PASS: {label}")
    else:
        print(f"FAIL: {label}")
        _fails += 1


def allowed(event, cwd, command="", root=ROOT):
    """Assert: exit 0, nothing on stderr (silent allow)."""
    code, _out, err = run(event, cwd, command, root)
    return code == 0 and err == ""


def blocked(event, cwd, command, root=ROOT):
    """Assert: exit 2, a message on stderr."""
    code, _out, err = run(event, cwd, command, root)
    return code == 2 and err != ""


def blocked_with(event, cwd, command, needle, root=ROOT):
    """Assert: exit 2 and `needle` appears in stderr."""
    code, _out, err = run(event, cwd, command, root)
    return code == 2 and needle in err
```

Keep the `_fails = 0` line that precedes these (do not remove it).

(d) Replace the entire worktree test region added in Task 2 (the
`# ── Worktree: ...` block through the `# ── Fallback: ...` block, current lines
~115-140) with:

```python
# ── Worktree (filesystem-detected): worktree root is the effective anchor ────
check("wt: `ls` at wt root allowed", allowed("PreToolUse", WT, "ls", root=PROJ))
check("wt: bare `cd WT` allowed", allowed("PreToolUse", WT, f"cd {WT}", root=PROJ))
check("wt: `cd WT && ls` allowed", allowed("PreToolUse", WT, f"cd {WT} && ls", root=PROJ))
check("wt: `cd subdir` blocked", blocked("PreToolUse", WT, "cd subdir", root=PROJ))
check("wt: `cd PROJ` (leave via cd) blocked",
      blocked("PreToolUse", WT, f"cd {PROJ} && git merge", root=PROJ))
check("wt: drift inside wt blocked", blocked("PreToolUse", WTSUB, "ls", root=PROJ))
check("wt: drift-inside-wt restore hint names wt root",
      blocked_with("PreToolUse", WTSUB, "ls", WT, root=PROJ))

# Main tree under the same PROJ root behaves normally
check("main: `ls` at PROJ allowed", allowed("PreToolUse", PROJ, "ls", root=PROJ))
check("main: ordinary drift at PROJ/subdir blocked", blocked("PreToolUse", PROJSUB, "ls", root=PROJ))

# Detection guards: foreign repo and spoofed .git file are NOT worktrees of PROJ
check("guard: foreign repo (.git dir) treated as drift", blocked("PreToolUse", OTHER, "ls", root=PROJ))
check("guard: spoofed .git file (gitdir outside PROJ) treated as drift",
      blocked("PreToolUse", EVIL, "ls", root=PROJ))

# PostToolUse against the worktree effective root
code, out, _err = run("PostToolUse", WT, "ls", root=PROJ)
check("wt: PostToolUse at wt root silent", code == 0 and out == "")

code, out, _err = run("PostToolUse", WTSUB, "ls", root=PROJ)
wt_warned = code == 0 and "additionalContext" in out
if wt_warned:
    parsed = json.loads(out)
    wt_warned = WTSUB in parsed["hookSpecificOutput"]["additionalContext"]
check("wt: PostToolUse drift inside wt warns", wt_warned)
```

- [ ] **Step 2: Run the suite — confirm the new worktree tests fail**

Run: `python3 tests/test_cwd_safety.py` Expected: FAIL. The hook still calls
`hook_input.get("worktree")` (always None now), so `E == PROJ` and the `WT`-cwd
"allowed" cases fail. The pre-existing Rule 1–5 fake-path tests still pass.

- [ ] **Step 3: Add the detection helpers to `scripts/cwd-safety.py`**

Insert these three helpers after the `_LEADING_CD` definition (after line ~39)
and before `main()`:

```python
def _is_under(path: str, parent: str) -> bool:
    """True if ``path`` equals ``parent`` or sits inside it.

    String containment only — no symlink/realpath resolution (the exact-match
    principle of DESIGN.md decision (d) is preserved for the cd match; this is
    only used to confirm a worktree's gitdir belongs to the project).
    """
    parent = parent.rstrip(os.sep)
    return path == parent or path.startswith(parent + os.sep)


def _read_gitdir(dotgit_file: str) -> str:
    """Absolute gitdir path from a worktree ``.git`` file, or "".

    A linked worktree's ``.git`` is a file containing ``gitdir: <path>``.
    Relative paths are resolved against the file's directory.
    """
    try:
        with open(dotgit_file, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ""
    for line in content.splitlines():
        if line.startswith("gitdir:"):
            path = line[len("gitdir:"):].strip()
            if not path:
                return ""
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(dotgit_file), path)
            return path
    return ""


def _worktree_root(cwd: str, project_dir: str) -> str:
    """Enclosing linked-worktree root if ``cwd`` is inside a git worktree of
    ``project_dir``, else "".

    Walks up from ``cwd``; the worktree root is the first ancestor whose
    ``.git`` is a *file* whose ``gitdir:`` resolves under
    ``project_dir/.git``. Stops at ``project_dir`` (the main working tree is
    not a linked worktree) and at the filesystem root. A ``.git`` *directory*
    (a nested main repo) is not a worktree. Filesystem reads only — no
    subprocess. Returns "" on anything unrecognized so the caller falls back
    to ``project_dir``.
    """
    if not cwd or not project_dir:
        return ""
    git_main = os.path.join(project_dir, ".git")
    d = cwd
    while True:
        if d == project_dir:
            return ""
        dotgit = os.path.join(d, ".git")
        if os.path.isfile(dotgit):
            gitdir = _read_gitdir(dotgit)
            if gitdir and _is_under(gitdir, git_main):
                return d
            return ""
        if os.path.isdir(dotgit):
            return ""
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent
```

- [ ] **Step 4: Swap the detection line in `main()`**

In `main()`, replace:

```python
    worktree = hook_input.get("worktree") or ""  # absent / null / "" → no worktree
    effective_root = worktree or project_dir
    in_worktree = bool(worktree)
```

with:

```python
    worktree = _worktree_root(cwd, project_dir)
    effective_root = worktree or project_dir
    in_worktree = bool(worktree)
```

Also update the `main()` docstring (it currently says "the active worktree root
when Claude Code reports one (the ``worktree`` field …)"):

```python
    """Dispatch on hook event; the effective root is always allowed.

    The effective root is the enclosing git-worktree root when ``cwd`` is
    inside a worktree of ``$CLAUDE_PROJECT_DIR`` (detected from the on-disk
    ``.git`` linkage), else ``$CLAUDE_PROJECT_DIR``.
    """
```

And update the module docstring lines 9-10 (which name `R` as
`($CLAUDE_PROJECT_DIR)`):

```python
PreToolUse(Bash) — decided against command ``C``, effective root ``R`` (the
enclosing git-worktree root when ``cwd`` is inside a worktree of
``$CLAUDE_PROJECT_DIR``, else ``$CLAUDE_PROJECT_DIR``) and current cwd ``W``
(``cwd`` from hook stdin):
```

- [ ] **Step 5: Run the suite — confirm all pass**

Run: `python3 tests/test_cwd_safety.py` Expected: PASS — `all tests passed`.
Worktree cases detected via the fixtures; the two guard cases (foreign `.git`
dir, spoofed `.git` file) correctly fall back to `E == PROJ` and block; all
pre-existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/cwd-safety.py tests/test_cwd_safety.py
git commit -m "feat: detect git worktree root from the filesystem"
```

---

### Task 4: Worktree-aware messages

Make the block/warn text say "active worktree root" and tell the agent to use
`ExitWorktree` (not `cd`) to leave, when a worktree is active.

**Files:**
- Modify: `tests/test_cwd_safety.py`
- Modify: `scripts/cwd-safety.py` (the three `_*_message` helpers)

- [ ] **Step 1: Add failing tests for worktree-aware wording**

Append to `tests/test_cwd_safety.py`, before the `if _fails:` block:

```python
# ── Worktree-aware messages ─────────────────────────────────────────────────
check("wt: cd-block message names ExitWorktree",
      blocked_with("PreToolUse", WT, "cd subdir", "ExitWorktree", root=PROJ))
check("wt: cd-to-main block names ExitWorktree",
      blocked_with("PreToolUse", WT, f"cd {PROJ} && git merge", "ExitWorktree", root=PROJ))
check("wt: drift-block message says 'worktree'",
      blocked_with("PreToolUse", WTSUB, "ls", "worktree", root=PROJ))

code, out, _err = run("PostToolUse", WTSUB, "ls", root=PROJ)
post_wt = code == 0 and "additionalContext" in out and "worktree" in out
check("wt: PostToolUse warning says 'worktree'", post_wt)

# Non-worktree messages must NOT mention ExitWorktree (no regression in wording)
_c, _o, err_nonwt = run("PreToolUse", ROOT, "cd subdir")
check("no wt: cd-block message omits ExitWorktree", "ExitWorktree" not in err_nonwt)
```

- [ ] **Step 2: Run the suite — confirm the new tests fail**

Run: `python3 tests/test_cwd_safety.py` Expected: FAIL — current helpers ignore
`in_worktree`; the non-worktree check passes.

- [ ] **Step 3: Implement worktree-aware branches in the three helpers**

Replace the three message helpers in `scripts/cwd-safety.py` with:

```python
def _cd_block_message(root: str, in_worktree: bool) -> str:
    """Rule 3 block text; worktree-aware when a worktree is active."""
    if in_worktree:
        return (
            "❌ Bash command blocked: `cd` away from the active worktree "
            "causes working-directory drift.\n"
            f"Active worktree root: {root}\n"
            "Instead: use absolute paths, run from the worktree root with "
            f"`cd {root} && <command>`, or scope the change to a subshell "
            "that does not persist, e.g. `(cd subdir && <command>)`.\n"
            "To leave the worktree entirely, use the ExitWorktree tool — not `cd`."
        )
    return (
        "❌ Bash command blocked: `cd` away from project root causes "
        "working-directory drift.\n"
        f"Project root: {root}\n"
        "Instead: use absolute paths, run from root with "
        f"`cd {root} && <command>`, or scope the change to a "
        "subshell that does not persist, e.g. `(cd subdir && <command>)`."
    )


def _drift_block_message(cwd: str, root: str, in_worktree: bool) -> str:
    """Rule 4 block text; worktree-aware when a worktree is active."""
    if in_worktree:
        return (
            "❌ Bash commands blocked: working directory is not the active "
            "worktree root.\n"
            f"Current: {cwd}\n"
            f"Run this command to restore: cd {root}\n"
            "To leave the worktree entirely, use the ExitWorktree tool."
        )
    return (
        "❌ Bash commands blocked: working directory is not project root.\n"
        f"Current: {cwd}\n"
        f"Run this command to restore: cd {root}"
    )


def _drift_warn_message(cwd: str, root: str, in_worktree: bool) -> str:
    """Rule 5 warning text; worktree-aware when a worktree is active."""
    if in_worktree:
        return (
            f"⚠️  Working directory is not the active worktree root: {cwd}\n"
            "Bash is blocked until cwd is restored.\n"
            f"Run: cd {root}"
        )
    return (
        f"⚠️  Working directory changed to: {cwd}\n"
        "Bash is blocked until cwd is restored.\n"
        f"Run: cd {root}"
    )
```

- [ ] **Step 4: Run the suite — confirm all pass**

Run: `python3 tests/test_cwd_safety.py`
Expected: PASS — `all tests passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/cwd-safety.py tests/test_cwd_safety.py
git commit -m "feat: worktree-aware block and warn messages"
```

---

### Task 5: Quality gate

- [ ] **Step 1: Run the precommit gate**

Run: `just precommit`
Expected: ends with `ok` and `all tests passed`.

- [ ] **Step 2: Sanity-probe against this real repo (it has a real `.git`)**

```bash
just probe PreToolUse "$(git rev-parse --show-toplevel)" "ls"
```
Expected: `exit: 0` (at the real project root, not a worktree → `E == R`).

No commit (verification only).

---

### Task 6: Documentation

**Files:** Modify `DESIGN.md`, `CLAUDE.md`.

- [ ] **Step 1: Amend FR2 in `DESIGN.md`**

Replace FR2 with:

```markdown
**FR2.** The hook reads `$CLAUDE_PROJECT_DIR` for the project root and `cwd`
from hook stdin. The **effective root** `E` is the enclosing git-worktree root
when `cwd` is inside a worktree of `$CLAUDE_PROJECT_DIR` — detected from the
on-disk `.git` linkage (a worktree's `.git` is a file whose `gitdir:` resolves
under `$CLAUDE_PROJECT_DIR/.git`) — otherwise `$CLAUDE_PROJECT_DIR`. All
decisions below are made against `E`.
```

- [ ] **Step 2: Restate FR3–FR7 against `E`**

Change `W == R` / `W != R` / "project root `R`" in FR3–FR7 to use `E`. Add to
FR5 (proactive cd block):

```markdown
When a worktree is active, this includes a `cd` back to `$CLAUDE_PROJECT_DIR`:
leaving a worktree is done with the `ExitWorktree` tool, not a raw `cd`. The
block message is worktree-aware and names `ExitWorktree`.
```

- [ ] **Step 3: Relax NFR1 and NFR3 in `DESIGN.md`**

NFR1 (determinism) and NFR3 (zero runtime dependencies / no I/O) currently
forbid I/O beyond the std streams. Append to each (do not delete the existing
text):

To NFR1:

```markdown
Worktree detection reads the filesystem (`os.path` stat calls and one `.git`
file); the hook is deterministic *given filesystem state*. It performs no
network access and spawns no subprocess.
```

To NFR3:

```markdown
**Exception:** worktree detection performs read-only filesystem access
(stat + reading a `.git` file). It remains subprocess-free and network-free.
```

- [ ] **Step 4: Add Design Decision (h) to `DESIGN.md`**

Insert after decision (g):

```markdown
### (h) Worktrees: single effective root, filesystem detection

**Decision:** When `cwd` is inside a git worktree of `$CLAUDE_PROJECT_DIR`, the
hook uses that worktree root as the single effective root `E`; otherwise
`E = $CLAUDE_PROJECT_DIR`. The worktree is detected from the on-disk `.git`
linkage — walk up from `cwd` to the first ancestor whose `.git` is a file whose
`gitdir:` resolves under `$CLAUDE_PROJECT_DIR/.git`. A `cd` back to the project
root while a worktree is active is blocked (use `ExitWorktree`).

**Rationale:** A worktree changes the session `cwd` while leaving
`$CLAUDE_PROJECT_DIR` at the original repo root, so the unmodified hook blocked
every command issued from a worktree. The correct anchor there is the worktree
root. Detection cannot use a hook payload field — empirical capture of a real
PreToolUse payload from inside a managed worktree showed **no `worktree` field
exists** (a contrary claim was confabulated from web search). It cannot use the
`.claude/worktrees/<name>` path convention either, because `EnterWorktree` can
enter a worktree at an arbitrary path and the location is relocatable. The git
`.git`-file linkage is the authoritative on-disk record and ties a worktree to
a specific main repo, so it covers every location and cannot be spoofed by
merely sitting in a directory named `worktrees`. A *single* effective root
(rather than accepting both roots) suffices because `ExitWorktree` restores
`cwd`, making the clean lifecycle "work in worktree → exit → merge from main";
no single Bash call legitimately needs both roots. The cost is read-only
filesystem access (see NFR1/NFR3); the exact-match principle of decision (d) is
preserved for the `cd E` command match — only detection reads the filesystem.

**Alternatives considered:** (1) Trust a `worktree` payload field — rejected, it
does not exist. (2) `.claude/worktrees/` path convention — rejected, arbitrary
and relocated worktrees defeat it. (3) Accept both `R` and the worktree root —
rejected; `ExitWorktree` makes exit-then-merge the clean path. (4) Have the hook
set the Bash cwd — impossible; hook output cannot redirect the tool's cwd.
```

- [ ] **Step 5: Update Limitations and History in `DESIGN.md`**

Add to Limitations:

```markdown
- **Worktree detection reads the filesystem.** Unlike the rest of the hook it
  is not pure-stdin; it stats ancestors of `cwd` and reads one `.git` file. A
  worktree whose `.git` linkage does not resolve under `$CLAUDE_PROJECT_DIR/.git`
  (e.g. a different repo) is treated as drift, not as a valid anchor.
```

Append to History:

```markdown
**2026-06 — honor git worktrees** (this repo). The hook now anchors to the
enclosing git-worktree root as a single effective root, detected from the
on-disk `.git` linkage (there is no worktree hook-payload field — verified
empirically), falling back to `$CLAUDE_PROJECT_DIR` otherwise. `cd` back to
main while a worktree is active is blocked in favor of `ExitWorktree`; block and
warn messages are worktree-aware. See decision (h).
```

- [ ] **Step 6: Update `CLAUDE.md`**

In "The behavioral contract", replace the opening sentence with one introducing
the effective root:

```markdown
Canonical statement lives in `DESIGN.md` (FR1–FR9). In short, at
`PreToolUse(Bash)` with **effective root `E`** (the enclosing git-worktree root
when `cwd` is inside a worktree of `$CLAUDE_PROJECT_DIR`, detected from the
on-disk `.git` linkage, else `$CLAUDE_PROJECT_DIR`) and cwd `W`:
```

Change the four numbered contract lines so each `R` reads `E`, and append to
item 3:

```markdown
   When a worktree is active this includes `cd $CLAUDE_PROJECT_DIR` — leave a
   worktree with the `ExitWorktree` tool, not `cd`.
```

Add a Conventions bullet:

```markdown
- **Effective root via filesystem detection.** `main()` computes
  `effective_root = _worktree_root(cwd, project_dir) or project_dir` and threads
  it into both handlers. `_worktree_root` walks up `cwd` and reads the `.git`
  linkage to recognize a worktree of the project — there is no payload field for
  this. Read-only filesystem access; the `cd E` match stays exact. See
  `DESIGN.md` → decision (h).
```

- [ ] **Step 7: Run the gate and commit**

```bash
just precommit
git add DESIGN.md CLAUDE.md
git commit -m "docs: document filesystem worktree detection"
```

---

## Self-review notes

- **Spec coverage:** filesystem detection helpers (Task 3) · single effective
  root + `E` plumbing (Task 2, reused) · `cd`-to-main blocked while active (Task
  3 tests + Rule 3) · detection guards for foreign/spoofed `.git` (Task 3 tests)
  · worktree-aware messages (Task 4) · verification finding (Task 1) · NFR
  relaxation + decision (h) (Task 6) · DESIGN.md + CLAUDE.md (Task 6).
- **No regression:** non-worktree branch of every message is the current wording
  verbatim; pre-existing fake-path tests use no fixtures and still pass because
  `_worktree_root` returns "" (reaches project_dir / finds no `.git`).
- **Name consistency:** `_worktree_root(cwd, project_dir)`,
  `_read_gitdir(dotgit_file)`, `_is_under(path, parent)`,
  `effective_root`/`in_worktree`, test helpers
  `run/allowed/blocked/blocked_with(..., root=ROOT)`, fixtures
  `PROJ/WT/WTSUB/PROJSUB/OTHER/EVIL`.
