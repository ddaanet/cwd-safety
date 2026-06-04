#!/usr/bin/env python3
"""Test harness for scripts/cwd-safety.py.

Drives the hook as a subprocess with crafted JSON on stdin, asserting the
exit code and the stream contents for every rule in the behavioral contract.
Stdlib only — no pytest, no third-party deps.

Run: python3 tests/test_cwd_safety.py   (exit 0 = all pass, 1 = failures)
"""

import json
import os
import subprocess
import sys

ROOT = "/project/root"  # the pretend $CLAUDE_PROJECT_DIR for these cases
SUB = "/project/root/subdir"
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "cwd-safety.py")

_fails = 0


def run(event, cwd, command=""):
    """Invoke the hook; return (exit_code, stdout, stderr)."""
    payload = {"hook_event_name": event, "cwd": cwd, "tool_input": {"command": command}}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=ROOT)
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


def allowed(event, cwd, command=""):
    """Assert: exit 0, nothing on stderr (silent allow)."""
    code, _out, err = run(event, cwd, command)
    return code == 0 and err == ""


def blocked(event, cwd, command):
    """Assert: exit 2, a message on stderr."""
    code, _out, err = run(event, cwd, command)
    return code == 2 and err != ""


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


if _fails:
    print(f"\n{_fails} test(s) failed")
    sys.exit(1)
print("\nall tests passed")
