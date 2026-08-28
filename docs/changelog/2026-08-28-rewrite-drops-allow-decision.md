# 2026-08-28 — The restore rewrite no longer settles the permission gate

`_rewrite_with_restore` emitted `permissionDecision: "allow"` alongside
`hookSpecificOutput.updatedInput`. That pairing means the hook, not the
permission pipeline, decides the call. A brief from the gitlore repo,
grounded in a read of the Claude Code 2.1.233 bundle and checked against
2.1.246–247 behaviour, states the caller's two paths:

- `updatedInput` **without** a decision — the caller replaces the working input
  and runs the full pipeline over it: deny and ask rules, the read-only
  auto-allow, then the auto-mode classifier.
- `updatedInput` **with** `permissionDecision: "allow"` — the only re-check is
  a narrow one for a matching deny rule, a matching ask rule, or two fixed ask
  reasons. A `passthrough` from rule matching yields `null` and the hook's
  allow stands. The classifier is never consulted.

So every FR5a and FR5c rewrite ran its tail with the classifier bypassed. The
hook inspects the leading `cd` and nothing else; the tail after the `&&` is
arbitrary. `cd sub && curl … | sh`, `cd tools && rm -rf …`, a `git push` to a
foreign remote — all pre-approved on the strength of a rule that only ever
meant "this `cd` does not persist". Nothing recorded it either: a Bash
`toolUseResult` carries no permission decision, so a bypassed classifier leaves
no trace in the transcript. The rewrite is the hook's main path — 591 rewrites
in 127 sessions by the survey behind decision (i) — so this was live on every
one of them.

The same construction retired the `unsandbox-git-status` plugin on 2026-08-26,
where it was confirmed live with `true && git status --porcelain` running
unchecked.

Both keys are gone. `updatedInput`, `additionalContext` and `systemMessage` are
unchanged, and the output already validated in that shape, so the rewrite still
lands and still saves the block-and-reissue turn. What changes is where the
rewritten command goes next: to the ordinary pipeline, where an unmodified
command would have gone. A read-only tail is auto-allowed locally; anything
else meets the classifier, one model call over cached context.

The bypass is not observable from inside a session — deny and ask rules are
re-checked under `allow` too, so neither discriminates, and classifier verdicts
are not persisted. The durable check is therefore the unit assertion, which was
*requiring* the bypass (`hso.get("permissionDecision") == "allow"`). It is
inverted to assert the key is absent, which reds the 24 rewrite tests against
the unchanged script and passes after.

Decision (i)'s "free on the security axis" paragraph is rewritten rather than
extended: the claim was true of the *command text* — the appended `cd E` is the
exact-match root, shell-quoted, the rest untouched — and false of the *gate*.
It now has to hold on both counts, and the argument for not pre-approving is
stated where the argument for mutating the command already lives.
