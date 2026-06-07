# cwd-safety worktree support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `cwd-safety` hook treat an active Claude Code managed worktree as the working-directory anchor, instead of blocking every command issued from it.

**Architecture:** Introduce a single *effective root* `E = (worktree field) or $CLAUDE_PROJECT_DIR`, computed once in `main()` and threaded into both handlers in place of the project dir. All existing rule logic is unchanged — only the value of "root" changes. Detection trusts solely the `worktree` field from the hook's stdin JSON (no path heuristics). Block/warn messages branch on whether a worktree is active. When no worktree is active, behavior is byte-for-byte identical to today.

**Tech Stack:** Python 3 stdlib only (`json`, `os`, `re`, `sys`). Tests are a stdlib subprocess driver (no pytest). Quality gate: `just precommit`.

**Spec:** `docs/superpowers/specs/2026-06-07-cwd-safety-worktree-design.md`

---

### Task 1: Verify the `worktree` payload field (gate)

This gates everything else. We trust only the `worktree` field, so its existence and exact spelling in the installed Claude Code version must be confirmed empirically — not assumed from web search.

**Files:**
- Temporary edit (reverted within this task): `scripts/cwd-safety.py`

- [ ] **Step 1: Add a temporary raw-stdin capture at the top of `main()`**

In `scripts/cwd-safety.py`, immediately after `hook_input = json.load(sys.stdin)`, temporarily add:

```python
    # TEMP — remove after verification (Task 1)
    with open("/tmp/cwd-safety-payload.json", "w") as _f:
        json.dump(hook_input, _f)
```

- [ ] **Step 2: Exercise it inside a real worktree**

In a live Claude Code session that has this plugin active:
1. `EnterWorktree` (create a throwaway worktree).
2. Run any trivial Bash command (e.g. `ls`) so PreToolUse fires.
3. Inspect the capture:

```bash
cat /tmp/cwd-safety-payload.json | python3 -m json.tool
```

Expected: a JSON object containing a key holding the absolute worktree root.

- [ ] **Step 3: Confirm the field name and record the version**

Verify the key is literally `worktree` and that its value is the worktree's absolute path. Note the Claude Code version (`claude --version`).

- **If the key is literally `worktree`:** proceed; the plan's code is correct as written.
- **If the key has a different name:** use that exact name everywhere this plan writes `hook_input.get("worktree")`.
- **If no such field exists / live verification is impossible in this environment:** proceed using `worktree` as the field name (best authoritative evidence is the in-session tool schema). The design's fallback guarantees safety — an absent field makes `E == R`, i.e. no behavior change — so implementing to a not-yet-present field cannot cause a regression. Record this as an open assumption in the commit message of Task 6.

- [ ] **Step 4: Remove the temporary capture and confirm a clean tree**

Delete the `# TEMP` block added in Step 1.

Run:
```bash
git diff --stat scripts/cwd-safety.py
```
Expected: no changes (the temp block is gone; the file matches HEAD).

```bash
rm -f /tmp/cwd-safety-payload.json
```

No commit in this task — it leaves the tree unchanged.

---

### Task 2: Effective root — core allow/block behavior

Add worktree support to the test harness, write failing tests for the core re-anchoring behavior, then implement the effective root.

**Files:**
- Test: `tests/test_cwd_safety.py`
- Modify: `scripts/cwd-safety.py` (`main`, `handle_pretooluse`, `handle_posttooluse`, `_is_cd_to_root`)

- [ ] **Step 1: Extend the test harness to carry a `worktree` field**

In `tests/test_cwd_safety.py`, add new constants after the existing `SUB` line (around line 17):

```python
WT = "/home/user/wt-feature"   # an active worktree root (deliberately OUT of tree:
WTSUB = WT + "/src"            # proves detection is field-based, not path-based)

_UNSET = object()  # sentinel: distinguishes "omit worktree" from null/"" /path
```

Replace the existing `run` function (lines 23-34) with a version that can set, omit, or null the field:

```python
def run(event, cwd, command="", worktree=_UNSET):
    """Invoke the hook; return (exit_code, stdout, stderr).

    worktree: _UNSET omits the key entirely; any other value (a path, "",
    or None) is placed in the payload verbatim (None serializes to JSON null).
    """
    payload = {"hook_event_name": event, "cwd": cwd, "tool_input": {"command": command}}
    if worktree is not _UNSET:
        payload["worktree"] = worktree
    env = dict(os.environ, CLAUDE_PROJECT_DIR=ROOT)
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr
```

Replace the `allowed` and `blocked` helpers (lines 46-55) so they thread `worktree` through, and add a content-asserting variant:

```python
def allowed(event, cwd, command="", worktree=_UNSET):
    """Assert: exit 0, nothing on stderr (silent allow)."""
    code, _out, err = run(event, cwd, command, worktree)
    return code == 0 and err == ""


def blocked(event, cwd, command, worktree=_UNSET):
    """Assert: exit 2, a message on stderr."""
    code, _out, err = run(event, cwd, command, worktree)
    return code == 2 and err != ""


def blocked_with(event, cwd, command, needle, worktree=_UNSET):
    """Assert: exit 2 and `needle` appears in stderr."""
    code, _out, err = run(event, cwd, command, worktree)
    return code == 2 and needle in err
```

- [ ] **Step 2: Add the failing core tests**

Append, just before the `if _fails:` block at the end of `tests/test_cwd_safety.py`:

```python
# ── Worktree: active worktree root is the effective anchor ───────────────────
check("wt active: `ls` at wt root allowed", allowed("PreToolUse", WT, "ls", worktree=WT))
check("wt active: bare `cd WT` allowed", allowed("PreToolUse", WT, f"cd {WT}", worktree=WT))
check("wt active: `cd WT && ls` allowed", allowed("PreToolUse", WT, f"cd {WT} && ls", worktree=WT))
check("wt active: `cd subdir` blocked", blocked("PreToolUse", WT, "cd subdir", worktree=WT))
check("wt active: `cd ROOT` (leave via cd) blocked",
      blocked("PreToolUse", WT, f"cd {ROOT} && git merge", worktree=WT))
check("wt active: drift inside wt blocked", blocked("PreToolUse", WTSUB, "ls", worktree=WT))
check("wt active: cwd at main (not wt root) blocked",
      blocked("PreToolUse", ROOT, "ls", worktree=WT))

# PostToolUse against the effective root
code, out, _err = run("PostToolUse", WT, "ls", worktree=WT)
check("wt active: PostToolUse at wt root silent", code == 0 and out == "")

code, out, _err = run("PostToolUse", WTSUB, "ls", worktree=WT)
wt_warned = code == 0 and "additionalContext" in out
if wt_warned:
    parsed = json.loads(out)
    wt_warned = WTSUB in parsed["hookSpecificOutput"]["additionalContext"]
check("wt active: PostToolUse drift inside wt warns", wt_warned)

# ── Fallback: absent / null / empty worktree behaves exactly like no worktree ─
check("worktree null: `ls` at ROOT allowed", allowed("PreToolUse", ROOT, "ls", worktree=None))
check("worktree empty: `ls` at ROOT allowed", allowed("PreToolUse", ROOT, "ls", worktree=""))
check("worktree null: drift at SUB blocked", blocked("PreToolUse", SUB, "ls", worktree=None))
```

- [ ] **Step 3: Run the suite to confirm the new tests fail**

Run:
```bash
python3 tests/test_cwd_safety.py
```
Expected: FAIL — the worktree-active cases fail because the hook still compares against `$CLAUDE_PROJECT_DIR`, so commands from `WT` are blocked (the "allowed" checks fail) and `cd ROOT` is wrongly allowed. The fallback (null/empty) cases should already pass.

- [ ] **Step 4: Implement the effective root in `main()`**

In `scripts/cwd-safety.py`, replace the body of `main()` (lines 42-55) with:

```python
def main() -> None:
    """Dispatch on hook event; the effective root is always allowed.

    The effective root is the active worktree root when Claude Code reports
    one (the ``worktree`` field in the hook payload), else ``$CLAUDE_PROJECT_DIR``.
    """
    hook_input = json.load(sys.stdin)

    event_name = hook_input.get("hook_event_name", "")
    cwd = hook_input.get("cwd", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    worktree = hook_input.get("worktree") or ""  # absent / null / "" → no worktree
    effective_root = worktree or project_dir
    in_worktree = bool(worktree)

    if event_name == "PreToolUse":
        handle_pretooluse(hook_input, cwd, effective_root, in_worktree)
    elif event_name == "PostToolUse":
        handle_posttooluse(cwd, effective_root, in_worktree)
    else:
        sys.exit(0)
```

- [ ] **Step 5: Thread the effective root through `handle_pretooluse`**

Replace `handle_pretooluse` (lines 76-105) with the version below. It keeps the rule logic identical but compares against `root` and delegates message text to helpers (added in Task 3; defined here as the current wording so this task is self-contained and green):

```python
def handle_pretooluse(hook_input: dict, cwd: str, root: str, in_worktree: bool) -> None:
    """Allow root-anchored commands; block drift-inducing cd and wrong-cwd work."""
    command = hook_input.get("tool_input", {}).get("command", "").strip()

    # Rule 2: the sanctioned root-anchored form is always allowed.
    if _is_cd_to_root(command, root):
        sys.exit(0)

    # Rule 3: any other leading `cd` is drift — block it before it happens,
    # even when already at the effective root.
    if _LEADING_CD.match(command):
        _block(_cd_block_message(root, in_worktree))

    # Rule 1: any other command from the effective root is fine.
    if cwd == root:
        sys.exit(0)

    # Rule 4: drift already happened — block until cwd is restored.
    _block(_drift_block_message(cwd, root, in_worktree))
```

- [ ] **Step 6: Thread the effective root through `handle_posttooluse`**

Replace `handle_posttooluse` (lines 108-126) with:

```python
def handle_posttooluse(cwd: str, root: str, in_worktree: bool) -> None:
    """Warn (non-blocking) after cwd drift is detected."""
    if cwd == root:
        sys.exit(0)

    warning = _drift_warn_message(cwd, root, in_worktree)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": warning,
        },
        "systemMessage": warning,
    }
    print(json.dumps(output))
```

- [ ] **Step 7: Rename the `_is_cd_to_root` parameter (no logic change)**

Replace `_is_cd_to_root` (lines 58-67) with the same function using `root` as the parameter name:

```python
def _is_cd_to_root(command: str, root: str) -> bool:
    """True if ``command`` is the sanctioned root-anchored form.

    Matches bare ``cd <root>`` and ``cd <root> && <rest>``, with the path
    unquoted, "double"-quoted, or 'single'-quoted. Rejects ``;`` and ``||``
    separators — only ``&&`` preserves the cd-first invariant.
    """
    escaped = re.escape(root)
    pattern = rf"""^cd\s+(?:{escaped}|"{escaped}"|'{escaped}')\s*(?:&&\s*.+)?$"""
    return bool(re.match(pattern, command))
```

- [ ] **Step 8: Add temporary message helpers so the file imports cleanly**

Task 5 adds the messages. So this task is self-contained and green, add these helpers now with the *current* (non-worktree) wording for both branches. Insert them after `_block` (after line 73):

```python
def _cd_block_message(root: str, in_worktree: bool) -> str:
    """Rule 3 block text (worktree-aware variant added in Task 3)."""
    return (
        "❌ Bash command blocked: `cd` away from project root causes "
        "working-directory drift.\n"
        f"Project root: {root}\n"
        "Instead: use absolute paths, run from root with "
        f"`cd {root} && <command>`, or scope the change to a "
        "subshell that does not persist, e.g. `(cd subdir && <command>)`."
    )


def _drift_block_message(cwd: str, root: str, in_worktree: bool) -> str:
    """Rule 4 block text (worktree-aware variant added in Task 3)."""
    return (
        "❌ Bash commands blocked: working directory is not project root.\n"
        f"Current: {cwd}\n"
        f"Run this command to restore: cd {root}"
    )


def _drift_warn_message(cwd: str, root: str, in_worktree: bool) -> str:
    """Rule 5 warning text (worktree-aware variant added in Task 3)."""
    return (
        f"⚠️  Working directory changed to: {cwd}\n"
        "Bash is blocked until cwd is restored.\n"
        f"Run: cd {root}"
    )
```

- [ ] **Step 9: Run the suite to confirm all tests pass**

Run:
```bash
python3 tests/test_cwd_safety.py
```
Expected: PASS — `all tests passed`. The worktree-active cases now pass; every pre-existing test still passes (with no worktree, `E == R`).

- [ ] **Step 10: Commit**

```bash
git add scripts/cwd-safety.py tests/test_cwd_safety.py
git commit -m "feat: honor active worktree as cwd-safety effective root"
```

---

### Task 3: Worktree-aware messages

Make the block/warn text say "active worktree root" and tell the agent to use `ExitWorktree` (not `cd`) to leave, when a worktree is active.

**Files:**
- Test: `tests/test_cwd_safety.py`
- Modify: `scripts/cwd-safety.py` (the three `_*_message` helpers)

- [ ] **Step 1: Add failing tests for worktree-aware wording**

Append to `tests/test_cwd_safety.py`, before the `if _fails:` block:

```python
# ── Worktree-aware messages ─────────────────────────────────────────────────
check("wt active: cd-block message names ExitWorktree",
      blocked_with("PreToolUse", WT, "cd subdir", "ExitWorktree", worktree=WT))
check("wt active: cd-to-main block names ExitWorktree",
      blocked_with("PreToolUse", WT, f"cd {ROOT} && git merge", "ExitWorktree", worktree=WT))
check("wt active: drift-block message says 'worktree'",
      blocked_with("PreToolUse", WTSUB, "ls", "worktree", worktree=WT))

code, out, _err = run("PostToolUse", WTSUB, "ls", worktree=WT)
post_wt = code == 0 and "additionalContext" in out and "worktree" in out
check("wt active: PostToolUse warning says 'worktree'", post_wt)

# Non-worktree messages must NOT mention ExitWorktree (no regression in wording)
_c, _o, err_nonwt = run("PreToolUse", ROOT, "cd subdir")
check("no wt: cd-block message omits ExitWorktree", "ExitWorktree" not in err_nonwt)
```

- [ ] **Step 2: Run the suite to confirm the new tests fail**

Run:
```bash
python3 tests/test_cwd_safety.py
```
Expected: FAIL — the worktree wording checks fail (current helpers ignore `in_worktree`); the non-worktree check passes.

- [ ] **Step 3: Implement worktree-aware branches in the three helpers**

In `scripts/cwd-safety.py`, replace the three helper bodies from Task 2 Step 8 with branched versions:

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

- [ ] **Step 4: Run the suite to confirm all tests pass**

Run:
```bash
python3 tests/test_cwd_safety.py
```
Expected: PASS — `all tests passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/cwd-safety.py tests/test_cwd_safety.py
git commit -m "feat: worktree-aware block and warn messages"
```

---

### Task 4: Quality gate

Confirm the full precommit gate is green (manifest + hooks.json validation, byte-compile, tests).

**Files:** none (verification only)

- [ ] **Step 1: Run the precommit gate**

Run:
```bash
just precommit
```
Expected: ends with `ok` and `all tests passed`; non-zero exit fails the gate.

- [ ] **Step 2: Sanity-probe a worktree case by hand (optional but recommended)**

The `probe` recipe does not set a `worktree` field, so probe the fallback path to confirm the hook still behaves at the real repo root:

```bash
just probe PreToolUse "$(git rev-parse --show-toplevel)" "ls"
```
Expected: `exit: 0` (allowed; no worktree → `E == R`).

No commit (verification only).

---

### Task 5: Documentation

Update `DESIGN.md` and `CLAUDE.md` to describe worktree handling.

**Files:**
- Modify: `DESIGN.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Amend FR2 in `DESIGN.md`**

Replace FR2 (the `$CLAUDE_PROJECT_DIR` / `cwd` requirement) with:

```markdown
**FR2.** The hook reads `$CLAUDE_PROJECT_DIR` for the project root `R`,
`cwd` from hook stdin for the current working directory `W`, and the
`worktree` field from hook stdin for the active managed-worktree root (if
any). The **effective root** `E` is the `worktree` value when present and
non-null, otherwise `R`. All decisions below are made against `E`.
```

- [ ] **Step 2: Restate FR3–FR7 against `E`**

In `DESIGN.md`, change each occurrence of "`W == R`" / "`W != R`" / "project root `R`" in FR3, FR4, FR5, FR6, and FR7 to use `E` instead of `R`. Add this clause to FR5 (proactive cd block):

```markdown
When a worktree is active, this includes a `cd` back to `$CLAUDE_PROJECT_DIR`:
leaving a worktree is done with the `ExitWorktree` tool, not a raw `cd`. The
block message is worktree-aware and names `ExitWorktree`.
```

- [ ] **Step 3: Add Design Decision (h) to `DESIGN.md`**

Insert after decision (g):

```markdown
### (h) Worktrees: single effective root, payload-field detection

**Decision:** When Claude Code reports an active managed worktree (the
`worktree` field in the hook's stdin JSON), the hook uses the worktree root as
the single effective root `E`; otherwise `E = $CLAUDE_PROJECT_DIR`. Detection
trusts only the `worktree` field — no path heuristics. A `cd` back to the
project root while a worktree is active is blocked (use `ExitWorktree`).

**Rationale:** A native worktree changes the session `cwd` while leaving
`$CLAUDE_PROJECT_DIR` at the original repo root, so the unmodified hook blocked
every command issued from a worktree. The worktree root is the correct anchor
there. A *single* effective root (rather than accepting both roots) is
sufficient because `ExitWorktree` restores `cwd` to the original directory and
clears the field, making the clean lifecycle "work in worktree → exit → merge
from main" — no single Bash call legitimately needs both roots at once.
Detection uses the `worktree` field rather than the `.claude/worktrees/<name>`
path convention because `EnterWorktree` can enter a worktree at an arbitrary
path (anything in `git worktree list`) and the location is relocatable via a
`WorktreeCreate` hook, so a path heuristic would yield false negatives. Trusting
only the field also preserves the exact-match principle of decision (d): no
prefix matching, no normalization. If the field is absent (older Claude Code),
`E` falls back to `R` and behavior is unchanged — the feature is inert, never a
regression.

**Alternatives considered:** (1) Accept both `R` and the worktree root
simultaneously — rejected; `ExitWorktree` makes exit-then-merge the clean path,
so the second root is unnecessary and widens the accepted-anchor set. (2)
Detect worktrees by the `.claude/worktrees/` path convention — rejected;
arbitrary-path and relocated worktrees defeat it. (3) Have the hook set the
Bash cwd — impossible; hook output cannot redirect the tool's working directory.
```

- [ ] **Step 4: Update Limitations and History in `DESIGN.md`**

Add to the Limitations list:

```markdown
- **Worktree detection depends on the `worktree` payload field.** If a Claude
  Code version does not provide it, an active worktree is not recognized and
  commands from it are blocked as drift (the pre-field behavior). This is a
  fail-safe, not a fail-open.
```

Append to the History section:

```markdown
**2026-06 — honor active worktrees** (this repo). The hook now anchors to the
active managed-worktree root (`worktree` field) as a single effective root,
falling back to `$CLAUDE_PROJECT_DIR` when none is active. `cd` back to main
while a worktree is live is blocked in favor of `ExitWorktree`; block/warn
messages are worktree-aware. See decision (h).
```

- [ ] **Step 5: Update the behavioral-contract summary in `CLAUDE.md`**

In the "The behavioral contract" section of `CLAUDE.md`, replace the opening sentence ("Canonical statement lives in `DESIGN.md`...") with one that introduces the effective root:

```markdown
Canonical statement lives in `DESIGN.md` (FR1–FR9). In short, at
`PreToolUse(Bash)` with **effective root `E`** (the active worktree root from
the `worktree` payload field when present, else `$CLAUDE_PROJECT_DIR`) and cwd
`W`:
```

Then change the four numbered contract lines below it so each `R` reads `E`,
and append to item 3 (the proactive `cd` block):

```markdown
   When a worktree is active this includes `cd E`'s counterpart `cd $CLAUDE_PROJECT_DIR`
   — leave a worktree with the `ExitWorktree` tool, not `cd`.
```

Also add a bullet to the "Conventions" section:

```markdown
- **Effective root, not just `$CLAUDE_PROJECT_DIR`.** `main()` computes
  `effective_root = (worktree field) or $CLAUDE_PROJECT_DIR` and threads it
  into both handlers. Detection trusts only the `worktree` field — no path
  heuristics, preserving the exact-match rule. See `DESIGN.md` → decision (h).
```

- [ ] **Step 6: Run the precommit gate (docs don't break it, but confirm)**

Run:
```bash
just precommit
```
Expected: `ok` / `all tests passed`.

- [ ] **Step 7: Commit**

```bash
git add DESIGN.md CLAUDE.md
git commit -m "docs: document worktree-aware effective root"
```

---

## Self-review notes

- **Spec coverage:** effective root (Task 2) · payload-field-only detection (Task 2 main()) · `cd`-to-main blocked while live (Task 2 tests + Rule 3) · worktree-aware messages (Task 3) · verification gate (Task 1) · tests incl. fallback null/empty (Task 2) · DESIGN.md + CLAUDE.md (Task 5). All spec sections map to a task.
- **No regression:** the non-worktree branch of every message is the current wording verbatim, and existing tests run with the field omitted (`E == R`).
- **Type/name consistency:** `effective_root`/`root`/`in_worktree`, `_is_cd_to_root(command, root)`, `_cd_block_message(root, in_worktree)`, `_drift_block_message(cwd, root, in_worktree)`, `_drift_warn_message(cwd, root, in_worktree)`, test helpers `run/allowed/blocked/blocked_with(..., worktree=_UNSET)` are used consistently across tasks.
