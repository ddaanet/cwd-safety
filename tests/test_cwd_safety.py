#!/usr/bin/env python3
"""Test harness for scripts/cwd-safety.py.

Drives the hook as a subprocess with crafted JSON on stdin, asserting the
exit code and the stream contents for every rule in the behavioral contract.
Stdlib only — no pytest, no third-party deps.

Run: python3 tests/test_cwd_safety.py   (exit 0 = all pass, 1 = failures)
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
WTDEEP = os.path.join(WT, "a", "b", "c") # a deeply nested subdir inside the worktree
PROJSUB = os.path.join(PROJ, "subdir")   # ordinary subdir of the main tree
OTHER = os.path.join(_TMP, "other")      # foreign repo: .git is a directory
EVIL = os.path.join(_TMP, "evil")        # spoof: .git file whose gitdir is outside PROJ
BINGIT = os.path.join(_TMP, "bingit")    # dir whose .git is non-UTF-8 bytes

os.makedirs(os.path.join(PROJ, ".git", "worktrees", "wt1"))
os.makedirs(PROJSUB)
os.makedirs(WTSUB)
os.makedirs(WTDEEP)
os.makedirs(os.path.join(OTHER, ".git"))
os.makedirs(EVIL)
os.makedirs(BINGIT)
with open(os.path.join(WT, ".git"), "w") as _f:
    _f.write("gitdir: " + os.path.join(PROJ, ".git", "worktrees", "wt1") + "\n")
with open(os.path.join(EVIL, ".git"), "w") as _f:
    _f.write("gitdir: " + os.path.join(_TMP, "elsewhere", ".git", "worktrees", "x") + "\n")
with open(os.path.join(BINGIT, ".git"), "wb") as _f:
    _f.write(b"\xff\xfe\x00 not utf-8")

_fails = 0


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


def rewritten(event, cwd, command, expected_cmd, root=ROOT):
    """Assert: a PreToolUse input rewrite.

    Exit 0, empty stderr, and an allow-decision JSON on stdout whose
    `updatedInput.command` equals `expected_cmd`. The rewrite is announced on
    both channels (agent: `additionalContext`, user: `systemMessage`), but the
    command itself is NOT echoed to the user — Claude Code already surfaces the
    rewritten command, so re-echoing it is bloat.
    """
    code, out, err = run(event, cwd, command, root)
    if code != 0 or err != "" or not out:
        return False
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return False
    hso = parsed.get("hookSpecificOutput", {})
    sysmsg = parsed.get("systemMessage", "")
    return (
        hso.get("permissionDecision") == "allow"
        and hso.get("updatedInput", {}).get("command") == expected_cmd
        and bool(hso.get("additionalContext"))  # agent is told cwd did not persist
        and bool(sysmsg)                         # user is notified
        and expected_cmd not in sysmsg           # but the command is not echoed to the user
    )


# ── Rule 1: at root, non-cd commands pass silently ──────────────────────────
check("at root: `ls` allowed", allowed("PreToolUse", ROOT, "ls -la"))
check("at root: pipeline allowed", allowed("PreToolUse", ROOT, "git status | head"))
check("at root: subshell cd allowed", allowed("PreToolUse", ROOT, "(cd subdir && ls)"))
check("at root: `lcd` not treated as cd", allowed("PreToolUse", ROOT, "lcd foo"))

# ── Rule 2: the root-anchored form is always allowed ────────────────────────
check("bare `cd ROOT` allowed at root", allowed("PreToolUse", ROOT, f"cd {ROOT}"))
check("`cd ROOT && ls` allowed at root", allowed("PreToolUse", ROOT, f"cd {ROOT} && ls"))
check('`cd "ROOT"` (quoted) allowed', allowed("PreToolUse", ROOT, f'cd "{ROOT}"'))
check("`cd ROOT` restores from drift", allowed("PreToolUse", SUB, f"cd {ROOT}"))
check("`cd ROOT && cmd` runs from drift", allowed("PreToolUse", SUB, f"cd {ROOT} && pytest"))

# ── Rule 3: drift-inducing `cd` blocked even at root ────────────────────────
check("at root: `cd subdir` blocked", blocked("PreToolUse", ROOT, "cd subdir"))
check("at root: `cd ..` blocked", blocked("PreToolUse", ROOT, "cd .."))
check("at root: `cd /tmp` blocked", blocked("PreToolUse", ROOT, "cd /tmp"))
check("at root: `cd -` blocked", blocked("PreToolUse", ROOT, "cd -"))
check("`cd ROOT; ls` blocked (only && allowed)", blocked("PreToolUse", ROOT, f"cd {ROOT}; ls"))
check("`cd ROOT || ls` blocked (only && allowed)", blocked("PreToolUse", ROOT, f"cd {ROOT} || ls"))

# ── Rule 3a: at root, `cd <subdir> && <cmd>` is rewritten to a non-persisting
#    subshell instead of blocked — saves the agent a turn, keeps cwd at root ──
check("at root: `cd subdir && ls` rewritten to subshell",
      rewritten("PreToolUse", ROOT, "cd subdir && ls", "(cd subdir && ls)"))
check("at root: `cd ../sib && make` rewritten to subshell",
      rewritten("PreToolUse", ROOT, "cd ../sib && make", "(cd ../sib && make)"))
check("at root: `cd /tmp && cmd` rewritten to subshell",
      rewritten("PreToolUse", ROOT, "cd /tmp && cmd", "(cd /tmp && cmd)"))
check("at root: multi-`&&` tail wrapped whole",
      rewritten("PreToolUse", ROOT, "cd a && b && c", "(cd a && b && c)"))
check("at root: `cd dir&&ls` (no spaces around &&) rewritten",
      rewritten("PreToolUse", ROOT, "cd dir&&ls", "(cd dir&&ls)"))
# Directory names with spaces: quoted or backslash-escaped
check('at root: `cd "my dir" && ls` (double-quoted, spaced) rewritten',
      rewritten("PreToolUse", ROOT, 'cd "my dir" && ls', '(cd "my dir" && ls)'))
check("at root: `cd 'my dir' && ls` (single-quoted, spaced) rewritten",
      rewritten("PreToolUse", ROOT, "cd 'my dir' && ls", "(cd 'my dir' && ls)"))
check("at root: `cd my\\ dir && ls` (escaped space) rewritten",
      rewritten("PreToolUse", ROOT, "cd my\\ dir && ls", "(cd my\\ dir && ls)"))
check("at root: `cd a b && ls` (two barewords, invalid cd) blocked",
      blocked("PreToolUse", ROOT, "cd a b && ls"))
check("at root: `cd subdir` (no &&) still blocked, not rewritten",
      blocked("PreToolUse", ROOT, "cd subdir"))
check("at root: `cd subdir; ls` not rewritten (only && wrappable)",
      blocked("PreToolUse", ROOT, "cd subdir; ls"))
check("at root: `cd subdir || ls` not rewritten",
      blocked("PreToolUse", ROOT, "cd subdir || ls"))
check("at root: bare `cd && ls` (no path) still blocked",
      blocked("PreToolUse", ROOT, "cd && ls"))
check("drifted: `cd deeper && ls` blocked, not rewritten (restore first)",
      blocked("PreToolUse", SUB, "cd deeper && ls"))

# ── Rule 4: from a drifted cwd, other commands are blocked ──────────────────
check("drifted: `ls` blocked", blocked("PreToolUse", SUB, "ls"))
check("drifted: `cd deeper` blocked", blocked("PreToolUse", SUB, "cd deeper"))

# ── Rule 5: PostToolUse warns only when drifted ─────────────────────────────
code, out, _err = run("PostToolUse", ROOT, "ls")
check("PostToolUse at root: silent, exit 0", code == 0 and out == "")

code, out, _err = run("PostToolUse", SUB, "ls")
warned = code == 0 and "additionalContext" in out
if warned:
    parsed = json.loads(out)
    warned = SUB in parsed["hookSpecificOutput"]["additionalContext"]
check("PostToolUse drifted: emits warning JSON, exit 0", warned)

# ── Unknown events are inert ────────────────────────────────────────────────
check("unknown event: exit 0, silent", allowed("SessionStart", SUB, "anything"))


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
check("wt: deep drift inside wt blocked, hint names wt root",
      blocked_with("PreToolUse", WTDEEP, "ls", WT, root=PROJ))
check("wt: `cd WT && cmd` from deep drift allowed",
      allowed("PreToolUse", WTDEEP, f"cd {WT} && pytest", root=PROJ))
check("wt: at wt root, `cd src && ls` rewritten to subshell",
      rewritten("PreToolUse", WT, "cd src && ls", "(cd src && ls)", root=PROJ))

# Main tree under the same PROJ root behaves normally
check("main: `ls` at PROJ allowed", allowed("PreToolUse", PROJ, "ls", root=PROJ))
check("main: ordinary drift at PROJ/subdir blocked", blocked("PreToolUse", PROJSUB, "ls", root=PROJ))

# Detection guards: foreign repo and spoofed .git file are NOT worktrees of PROJ
check("guard: foreign repo (.git dir) treated as drift", blocked("PreToolUse", OTHER, "ls", root=PROJ))
check("guard: spoofed .git file (gitdir outside PROJ) treated as drift",
      blocked("PreToolUse", EVIL, "ls", root=PROJ))
check("guard: non-UTF-8 .git file treated as drift, not a crash",
      blocked("PreToolUse", BINGIT, "ls", root=PROJ))

# PostToolUse against the worktree effective root
code, out, _err = run("PostToolUse", WT, "ls", root=PROJ)
check("wt: PostToolUse at wt root silent", code == 0 and out == "")

code, out, _err = run("PostToolUse", WTSUB, "ls", root=PROJ)
wt_warned = code == 0 and "additionalContext" in out
if wt_warned:
    parsed = json.loads(out)
    wt_warned = WTSUB in parsed["hookSpecificOutput"]["additionalContext"]
check("wt: PostToolUse drift inside wt warns", wt_warned)


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


if _fails:
    print(f"\n{_fails} test(s) failed")
    sys.exit(1)
print("\nall tests passed")
