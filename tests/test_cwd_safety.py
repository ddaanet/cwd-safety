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

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "cwd-safety.py")

# ── Real on-disk fixtures ───────────────────────────────────────────────────
# Two reasons the roots must exist on disk: worktree detection reads the `.git`
# linkage, and the fail-open rule calls `os.path.isdir(root)` — a fake root would
# fail open and silently allow everything, gutting the drift/block assertions.
_TMP = tempfile.mkdtemp(prefix="cwdsafety-test-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

ROOT = os.path.join(_TMP, "root")        # non-worktree project root (must exist)
SUB = os.path.join(ROOT, "subdir")       # a drifted cwd under the main root
GONE = os.path.join(_TMP, "deleted-root")  # NEVER created — a deleted effective root
PROJ = os.path.join(_TMP, "proj")        # a real project root
WT = os.path.join(_TMP, "wt1")           # worktree root OUTSIDE proj (location-independent)
WTSUB = os.path.join(WT, "src")          # a subdir inside the worktree
WTDEEP = os.path.join(WT, "a", "b", "c") # a deeply nested subdir inside the worktree
PROJSUB = os.path.join(PROJ, "subdir")   # ordinary subdir of the main tree
OTHER = os.path.join(_TMP, "other")      # foreign repo: .git is a directory
EVIL = os.path.join(_TMP, "evil")        # spoof: .git file whose gitdir is outside PROJ
BINGIT = os.path.join(_TMP, "bingit")    # dir whose .git is non-UTF-8 bytes
SPACED = os.path.join(_TMP, "my root")   # effective root whose path needs quoting

os.makedirs(SUB)  # also creates ROOT; GONE is deliberately left absent
os.makedirs(os.path.join(PROJ, ".git", "worktrees", "wt1"))
os.makedirs(PROJSUB)
os.makedirs(WTSUB)
os.makedirs(WTDEEP)
os.makedirs(os.path.join(OTHER, ".git"))
os.makedirs(EVIL)
os.makedirs(BINGIT)
os.makedirs(SPACED)
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


def restore(command, eff_root):
    """The FR5a/FR5c rewrite shape: the command as written, then a newline and a
    `cd` back to the effective root. A newline — not `;` or `&&` — so a trailing
    heredoc or comment in `command` survives; no `( … )`, which would hide the
    command's segments from the sandbox `excludedCommands` matcher."""
    return f"{command}\ncd {eff_root}"


# ── FR3: at root, non-cd commands pass silently ─────────────────────────────
check("at root: `ls` allowed", allowed("PreToolUse", ROOT, "ls -la"))
check("at root: pipeline allowed", allowed("PreToolUse", ROOT, "git status | head"))
check("at root: subshell cd allowed", allowed("PreToolUse", ROOT, "(cd subdir && ls)"))
check("at root: `lcd` not treated as cd", allowed("PreToolUse", ROOT, "lcd foo"))

# ── FR4: the root-anchored form is always allowed ───────────────────────────
check("bare `cd ROOT` allowed at root", allowed("PreToolUse", ROOT, f"cd {ROOT}"))
check("`cd ROOT && ls` allowed at root", allowed("PreToolUse", ROOT, f"cd {ROOT} && ls"))
check('`cd "ROOT"` (quoted) allowed', allowed("PreToolUse", ROOT, f'cd "{ROOT}"'))
check("`cd ROOT` restores from drift", allowed("PreToolUse", SUB, f"cd {ROOT}"))
check("`cd ROOT && cmd` runs from drift", allowed("PreToolUse", SUB, f"cd {ROOT} && pytest"))

# ── FR5: drift-inducing `cd` blocked even at root ───────────────────────────
check("at root: `cd subdir` blocked", blocked("PreToolUse", ROOT, "cd subdir"))
check("at root: `cd ..` blocked", blocked("PreToolUse", ROOT, "cd .."))
check("at root: `cd /tmp` blocked", blocked("PreToolUse", ROOT, "cd /tmp"))
check("at root: `cd -` blocked", blocked("PreToolUse", ROOT, "cd -"))
check("`cd ROOT; ls` blocked (only && allowed)", blocked("PreToolUse", ROOT, f"cd {ROOT}; ls"))
check("`cd ROOT || ls` blocked (only && allowed)", blocked("PreToolUse", ROOT, f"cd {ROOT} || ls"))

# ── FR5a: at root, `cd <subdir> && <cmd>` gets a `cd <root>` restore appended
#    instead of blocked — saves the agent a turn, keeps cwd at root ──────────
check("at root: `cd subdir && ls` rewritten with restore",
      rewritten("PreToolUse", ROOT, "cd subdir && ls", restore("cd subdir && ls", ROOT)))
check("at root: `cd ../sib && make` rewritten with restore",
      rewritten("PreToolUse", ROOT, "cd ../sib && make", restore("cd ../sib && make", ROOT)))
check("at root: `cd /tmp && cmd` rewritten with restore",
      rewritten("PreToolUse", ROOT, "cd /tmp && cmd", restore("cd /tmp && cmd", ROOT)))
check("at root: multi-`&&` tail restore appended",
      rewritten("PreToolUse", ROOT, "cd a && b && c", restore("cd a && b && c", ROOT)))
check("at root: `cd dir&&ls` (no spaces around &&) rewritten",
      rewritten("PreToolUse", ROOT, "cd dir&&ls", restore("cd dir&&ls", ROOT)))
# Directory names with spaces: quoted or backslash-escaped
check('at root: `cd "my dir" && ls` (double-quoted, spaced) rewritten',
      rewritten("PreToolUse", ROOT, 'cd "my dir" && ls', restore('cd "my dir" && ls', ROOT)))
check("at root: `cd 'my dir' && ls` (single-quoted, spaced) rewritten",
      rewritten("PreToolUse", ROOT, "cd 'my dir' && ls", restore("cd 'my dir' && ls", ROOT)))
check("at root: `cd my\\ dir && ls` (escaped space) rewritten",
      rewritten("PreToolUse", ROOT, "cd my\\ dir && ls", restore("cd my\\ dir && ls", ROOT)))
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

# The rewrite is a flat list, not a subshell: a trailing heredoc survives, and
# the appended restore is shell-quoted.
check("at root: trailing heredoc survives the rewrite",
      rewritten("PreToolUse", ROOT, "cd sub && cat <<'EOF'\nhi\nEOF",
                restore("cd sub && cat <<'EOF'\nhi\nEOF", ROOT)))
check("spaced root: appended restore is quoted",
      rewritten("PreToolUse", SPACED, "cd sub && ls", f"cd sub && ls\ncd '{SPACED}'", root=SPACED))
_c, _o, _e = run("PreToolUse", ROOT, "cd sub && ls")
_hso = json.loads(_o)["hookSpecificOutput"]
check("rewrite notes do not advertise a subshell",
      "subshell" not in _hso["additionalContext"].lower()
      and "subshell" not in json.loads(_o)["systemMessage"].lower())
check("rewrite agent note says cwd is restored to root",
      "restor" in _hso["additionalContext"].lower())

# ── FR6: from a drifted cwd, other commands are blocked ─────────────────────
check("drifted: `ls` blocked", blocked("PreToolUse", SUB, "ls"))
check("drifted: `cd deeper` blocked", blocked("PreToolUse", SUB, "cd deeper"))

# ── FR7: PostToolUse warns only when drifted ────────────────────────────────
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
check("wt: at wt root, `cd src && ls` rewritten with restore",
      rewritten("PreToolUse", WT, "cd src && ls", restore("cd src && ls", WT), root=PROJ))

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


# ── FR: redirections between the cd target and `&&` are tolerated ────────────
# A redirection (2>&1, >f, 2>/dev/null …) does not change the cd-first `&&`
# semantics, so the root-anchored allow and the subshell rewrite must see past it.
check("redir: `cd ROOT 2>&1 && ls` allowed at root",
      allowed("PreToolUse", ROOT, f"cd {ROOT} 2>&1 && ls"))
check("redir: bare `cd ROOT 2>&1` allowed at root",
      allowed("PreToolUse", ROOT, f"cd {ROOT} 2>&1"))
check("redir: `cd ROOT >o 2>&1 && ls` (two redirs) allowed",
      allowed("PreToolUse", ROOT, f"cd {ROOT} >o 2>&1 && ls"))
check("redir: `cd ROOT 2>&1 && cmd` restores from drift",
      allowed("PreToolUse", SUB, f"cd {ROOT} 2>&1 && pytest"))
check("redir: at root `cd subdir 2>&1 && ls` rewritten with restore",
      rewritten("PreToolUse", ROOT, "cd subdir 2>&1 && ls", restore("cd subdir 2>&1 && ls", ROOT)))
check("redir: at root `cd subdir >out.txt && ls` rewritten with restore",
      rewritten("PreToolUse", ROOT, "cd subdir >out.txt && ls", restore("cd subdir >out.txt && ls", ROOT)))
# Guardrail: the separator must still be `&&` — a redirect does not license `;`/newline.
check("redir: `cd ROOT 2>&1; ls` still blocked (only && allowed)",
      blocked("PreToolUse", ROOT, f"cd {ROOT} 2>&1; ls"))
check("redir: `cd subdir 2>&1; ls` still blocked, not rewritten",
      blocked("PreToolUse", ROOT, "cd subdir 2>&1; ls"))

# ── FR: embedded `cd` after a top-level separator is blocked (narrow) ────────
# `mkdir … && cd sub && …` drifts even though `cd` is not the leading token.
check("embedded: `mkdir -p t && cd t && ls` blocked at root",
      blocked("PreToolUse", ROOT, "mkdir -p t && cd t && ls"))
check("embedded: `echo hi; cd sub` blocked at root",
      blocked("PreToolUse", ROOT, "echo hi; cd sub"))
check("embedded: newline-joined `pwd\\ncd sub` blocked at root",
      blocked("PreToolUse", ROOT, "pwd\ncd sub"))
check("embedded: block message offers the `cd <subdir> && <command>` form",
      blocked_with("PreToolUse", ROOT, "mkdir -p t && cd t && ls", "cd <subdir> && <command>"))
_c, _o, _e = run("PreToolUse", ROOT, "mkdir -p t && cd t && ls")
check("block messages never advertise a `( … )` subshell", "(cd" not in _e)
_c, _o, _e = run("PreToolUse", WT, "cd ..", root=PROJ)
check("worktree block message never advertises a `( … )` subshell", "(cd" not in _e)
# The subshell form we recommend everywhere must NOT be caught (paren before cd).
check("embedded: `mkdir x && (cd x && ls)` subshell allowed",
      allowed("PreToolUse", ROOT, "mkdir x && (cd x && ls)"))
check("embedded: `foo | cd x` (pipe subshell) allowed",
      allowed("PreToolUse", ROOT, "foo | cd x"))
# FR5b masks quoted strings, heredoc bodies and parenthesised regions before
# looking for a separator-adjacent `cd`, so text that only *mentions* a `cd`
# never blocks, and a `cd` that is not the first statement of a subshell is
# seen for the subshell it is.
check("embedded: double-quoted `&& cd` literal allowed",
      allowed("PreToolUse", ROOT, 'echo "x && cd y"'))
check("embedded: single-quoted `; cd` literal allowed",
      allowed("PreToolUse", ROOT, "echo 'x; cd y'"))
check("embedded: escaped-quote juggling `'it'\"'\"'s'` still sees the real `&& cd`",
      blocked("PreToolUse", ROOT, "echo 'it'\"'\"'s' && cd sub"))
check("embedded: heredoc body mentioning `&& cd` allowed",
      allowed("PreToolUse", ROOT, "python3 - <<'EOF'\nimport os\n# foo && cd sub\nEOF"))
check("embedded: `<<-` heredoc body with tab-indented delimiter allowed",
      allowed("PreToolUse", ROOT, "cat <<-EOF\n\tx; cd sub\n\tEOF"))
check("embedded: `cd` after a heredoc's closing delimiter still blocked",
      blocked("PreToolUse", ROOT, "cat <<EOF\nbody\nEOF\ncd sub"))
check("embedded: `cd` on the heredoc's own command line still blocked",
      blocked("PreToolUse", ROOT, "cat <<EOF && cd sub\nbody\nEOF"))
check("embedded: `(set -e; cd x; make)` subshell allowed",
      allowed("PreToolUse", ROOT, "(set -e; cd x; make)"))
check("embedded: `$(cd sub && pwd)` substitution allowed",
      allowed("PreToolUse", ROOT, "x=$(cd sub && pwd); echo $x"))
check("embedded: `$(cd sub && pwd)` inside double quotes allowed",
      allowed("PreToolUse", ROOT, 'echo "$(cd sub && pwd)" && ls'))
check("embedded: `<(cd sub && ls)` process substitution allowed",
      allowed("PreToolUse", ROOT, "diff <(cd sub && ls) <(ls)"))
check("embedded: backtick `cd` allowed",
      allowed("PreToolUse", ROOT, "x=`cd sub && pwd`; echo $x"))
check("embedded: `case` pattern parens do not hide a later top-level `cd`",
      blocked("PreToolUse", ROOT, "case x in a) echo a;; esac; cd sub"))
check("embedded: arithmetic `$((1+2))` does not hide a later `cd`",
      blocked("PreToolUse", ROOT, "x=$((1+2)); cd sub"))
# A `cd` in the body of a compound statement runs in the current shell too.
check("embedded: `if …; then cd sub; fi` blocked",
      blocked("PreToolUse", ROOT, "if true; then cd sub; fi"))
check("embedded: `for …; do cd $d; done` blocked",
      blocked("PreToolUse", ROOT, "for d in a b; do cd $d; done"))
check("embedded: `true && { cd sub; }` group blocked",
      blocked("PreToolUse", ROOT, "true && { cd sub; }"))
check("embedded: `f() { cd sub; }; f` blocked (a brace group is a body)",
      blocked("PreToolUse", ROOT, "f() { cd sub; }; f"))
check("embedded: `then`/`do` inside a word (`thence cd`) is not a separator",
      allowed("PreToolUse", ROOT, "echo thence cd sub"))
check("embedded: `#` comment mentioning `&& cd` allowed",
      allowed("PreToolUse", ROOT, "ls # then && cd sub\necho done"))
check("embedded: `$#` is not a comment, the `&& cd` after it still blocks",
      blocked("PreToolUse", ROOT, "echo $# && cd sub"))
check("embedded: a `cd` on the line after a comment still blocks",
      blocked("PreToolUse", ROOT, "ls # note\ncd sub"))
# An unterminated quote masks to the end and falls open to PostToolUse; bash
# refuses the command anyway.
check("embedded: unterminated quote before `&& cd` allowed (fail open)",
      allowed("PreToolUse", ROOT, "echo 'x && cd sub"))

# ── FR5c: a `set -e` script with an embedded `cd` gets a restore appended ─────
# A `set -e`-first script is the agent's declared fail-fast intent; `set -e` is
# inert under the Bash tool, so the appended restore — not errexit — is what
# keeps cwd at root (decision (l)). This only *replaces* the FR5b block: it fires
# only when there is an embedded `cd` to catch.
check("set-e: `set -e\\ncd t\\nmake` restore appended",
      rewritten("PreToolUse", ROOT, "set -e\ncd tools\nmake build", restore("set -e\ncd tools\nmake build", ROOT)))
check("set-e: `set -euo pipefail; cd b; make` restore appended",
      rewritten("PreToolUse", ROOT, "set -euo pipefail; cd build; make", restore("set -euo pipefail; cd build; make", ROOT)))
check("set-e: `set -e && cd sub && ls` restore appended",
      rewritten("PreToolUse", ROOT, "set -e && cd sub && ls", restore("set -e && cd sub && ls", ROOT)))
check("set-e: `set -o errexit` long form restore appended",
      rewritten("PreToolUse", ROOT, "set -o errexit\ncd x\nmake", restore("set -o errexit\ncd x\nmake", ROOT)))
check("set-e: `set -ex` (errexit + xtrace) restore appended",
      rewritten("PreToolUse", ROOT, "set -ex\ncd x\nmake", restore("set -ex\ncd x\nmake", ROOT)))
check("set-e: leading comment before `set -e` tolerated",
      rewritten("PreToolUse", ROOT, "# build helper\nset -e\ncd t\nmake", restore("# build helper\nset -e\ncd t\nmake", ROOT)))
# The rewrite announcement is tailored to the set -e case (agent note present).
_c, se_out, _e = run("PreToolUse", ROOT, "set -e\ncd t\nmake")
se_note = _c == 0 and se_out and "set -e" in json.loads(se_out)["hookSpecificOutput"]["additionalContext"]
check("set-e: agent note mentions set -e", se_note)
check("set-e: notes do not advertise a subshell",
      _c == 0 and "subshell" not in json.loads(se_out)["hookSpecificOutput"]["additionalContext"].lower()
      and "subshell" not in json.loads(se_out)["systemMessage"].lower())

# Exclusions — all fall through to the FR5b block (errexit not guaranteed before cd).
check("set-e: `set +e` (errexit disabled) blocked, not restore appended",
      blocked("PreToolUse", ROOT, "set +e\ncd x\nmake"))
check("set-e: `set -u` (no errexit) blocked, not restore appended",
      blocked("PreToolUse", ROOT, "set -u\ncd x\nmake"))
check("set-e: `set -o pipefail` (no errexit) blocked, not restore appended",
      blocked("PreToolUse", ROOT, "set -o pipefail\ncd x\nmake"))
check("set-e: bare `set` (prints vars, no errexit) blocked, not restore appended",
      blocked("PreToolUse", ROOT, "set\ncd x\nmake"))
check("set-e: `set -e` not first statement blocked, not restore appended",
      blocked("PreToolUse", ROOT, "foo\nset -e\ncd x\nmake"))
check("set-e: `setup && cd x` not treated as `set` (word boundary)",
      blocked("PreToolUse", ROOT, "setup && cd x && make"))
# No embedded cd: the set -e script is left alone (allow-silent, not wrapped).
check("set-e: `set -e\\nmake\\nmake test` (no cd) allowed silently",
      allowed("PreToolUse", ROOT, "set -e\nmake\nmake test"))
# From a drifted cwd the same script is blocked — restore the root first.
check("set-e: drifted `set -e\\ncd x` blocked (restore first)",
      blocked("PreToolUse", SUB, "set -e\ncd x\nmake"))
# Worktree: wrapped uniformly, including an embedded cd to the main repo (the
# subshell keeps cwd in the worktree — no cross-root carve-out, unlike FR5a).
check("set-e wt: `set -e\\ncd src\\nmake` at wt root restore appended",
      rewritten("PreToolUse", WT, "set -e\ncd src\nmake", restore("set -e\ncd src\nmake", WT), root=PROJ))
check("set-e wt: embedded `cd MAIN` in a set -e script still restore appended",
      rewritten("PreToolUse", WT, f"set -e\ncd {PROJ}\ngit log", restore(f"set -e\ncd {PROJ}\ngit log", WT), root=PROJ))

# ── FR: $CLAUDE_PROJECT_DIR is itself a linked worktree → treated as a plain root ─
# The shape-2 self-destruct guard was removed: its `ExitWorktree` advice was a
# dead end (that tool is a no-op for a background worktree session it did not
# create). With CPD == the worktree path and no *enclosing* project dir,
# `_worktree_root` finds nothing, so the hook governs it as an ordinary root
# E=CPD. A `cd <main> && …` is then a benign subdir-style subshell rewrite (not a
# cross-root block), and drift blocks with a plain `cd <CPD>` hint — no
# `ExitWorktree`. The now-survivable self-destruct is covered by fail-open below.
check("cpd-wt: `ls` at CPD=worktree allowed", allowed("PreToolUse", WT, "ls", root=WT))
check("cpd-wt: `cd src && ls` rewritten with restore",
      rewritten("PreToolUse", WT, "cd src && ls", restore("cd src && ls", WT), root=WT))
check("cpd-wt: `cd MAIN && cmd` rewritten with restore (no cross-root block)",
      rewritten("PreToolUse", WT, f"cd {PROJ} && git worktree remove x", restore(f"cd {PROJ} && git worktree remove x", WT), root=WT))
_c, _o, cpd_err = run("PreToolUse", WTSUB, "ls", root=WT)
check("cpd-wt: drift blocks with plain `cd CPD` hint, no ExitWorktree",
      _c == 2 and WT in cpd_err and "ExitWorktree" not in cpd_err and "worktree" not in cpd_err)


# ── FR: fail open when the effective root no longer exists on disk ────────────
# A session whose effective root E was deleted out from under it (a worktree
# removed via `git worktree remove --force <self>`; the shell then falls back to
# the main repo) must not be bricked. With E gone the guard's contract ("keep cwd
# at E") is unsatisfiable, so PreToolUse steps aside — allow-silent, before every
# rule, so even a leading `cd` is freed — and PostToolUse swaps the impossible
# `cd E` restore hint for a "guard disabled — restart" notice.
check("failopen: `ls` allowed when root deleted (W==E)",
      allowed("PreToolUse", GONE, "ls", root=GONE))
check("failopen: `ls` allowed when root deleted (W!=E, shell fell back to main)",
      allowed("PreToolUse", PROJ, "ls", root=GONE))
check("failopen: arbitrary `cd /tmp` allowed when root deleted",
      allowed("PreToolUse", PROJ, "cd /tmp", root=GONE))
check("failopen: `mkdir -p x && cd x` allowed when root deleted",
      allowed("PreToolUse", PROJ, "mkdir -p x && cd x", root=GONE))
check("failopen: even a leading `cd subdir` allowed when root deleted",
      allowed("PreToolUse", GONE, "cd subdir", root=GONE))
# PostToolUse: replacement warning — names the root, says disabled/restart, and
# omits the now-impossible `cd E` restore hint.
_c, fo_out, _e = run("PostToolUse", PROJ, "ls", root=GONE)
fo = _c == 0 and "additionalContext" in fo_out
if fo:
    fo_ctx = json.loads(fo_out)["hookSpecificOutput"]["additionalContext"]
    fo = (GONE in fo_ctx and "disabled" in fo_ctx and "Restart" in fo_ctx
          and "Run: cd" not in fo_ctx and "restore" not in fo_ctx.lower())
check("failopen: PostToolUse warns 'guard disabled — restart', no `cd E` hint", fo)
# A real-but-oddly-spelled root (trailing slash) is live, not deleted: no fail-open.
check("failopen: trailing-slash live root is not seen as deleted",
      blocked("PreToolUse", SUB, "ls", root=ROOT + os.sep))


if _fails:
    print(f"\n{_fails} test(s) failed")
    sys.exit(1)
print("\nall tests passed")
