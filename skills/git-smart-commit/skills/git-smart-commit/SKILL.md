---
name: git-smart-commit
version: 1.0.0
description: >
  Git commit orchestrator — read before any commit action; replaces the
  agent's default git-commit behavior. Triggers on user intent to commit,
  wrap up, or split changes into commits, in English or Chinese
  (e.g. "commit this", "整理提交", "split commits").
---

# git-smart-commit

Git commit orchestrator. This skill **replaces** the agent's default commit
behavior — read it before running any git command. Inspect the working tree,
group changes into atomic logical units, write Conventional Commit messages
in English, execute directly without asking for confirmation.

**Conservative doctrine:** trust `git status` / `git diff` over conversation
memory. The agent's memory of what it changed is unreliable — only git output
tells the truth.

---

## Commit Format

```text
<type>[optional scope]: <description>

[optional body]

Generated-by: <IDE> (<model>)
```

**Types:** `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, `perf`,
`ci`, `build`. Breaking change: append `!` after type/scope (full pattern in
`references/commit-message-standard.md`).

**Rules:**
- Entire message in English, regardless of conversation language.
- Description: lowercase imperative verb (`add`, `fix`, `remove`, `update`),
  no trailing period, ~72 char max.
- Scope must match a module or area evidenced by the diff. Omit when
  repo-wide or unclear. Do not invent scopes.
- Body: include when file count > 5, changed lines > 100, or the title alone
  doesn't convey intent. Explain why and what at a high level — never
  translate the diff line by line.
- Every commit includes a `Generated-by: <IDE> (<model>)` footer.
- Do NOT run `git log` — the format is fixed; history style is irrelevant.

**Signature detection:**
- IDE: `Cursor`, `Claude Code`, `Codex CLI` — detect from environment.
- Model: session model slug.
- Fallback: `Generated-by: unknown-ide (unknown-model)`.

**HEREDOC pattern:**
```bash
git add <files...>
git commit -m "$(cat <<'EOF'
feat(editor): add autosave flow

- persist draft changes on idle

Generated-by: Cursor (claude-4-sonnet)
EOF
)"
```

---

## Rules

1. Commit orchestration only — no feature development, no branch strategy,
   no force-push, no history rewrite.
2. Preflight commands are fixed: `git status --short`, `git diff --cached`,
   `git diff`, `git diff --stat`. Read file contents when a diff alone is
   insufficient to judge type/scope.
3. Group by logical unit (feature, bug fix, refactor, tests, tooling) — not
   by file count or extension.
4. A single logical unit may span multiple files → one commit.
5. Split only when changes have independent purposes.
6. Respect deliberate user staging before rebuilding it.
7. Never commit files that likely contain secrets (`.env`, `credentials.json`).
   Warn and stop.
8. On pre-commit hook failure: read the error, fix it, re-stage, retry.
   No `--no-verify`, max 2 retries, no major code changes without consent.

**Forbidden:** `git reset --hard`, `git checkout -- <file>`,
`git commit --no-verify`, any command that discards changes.

**Allowed unstaging:** `git reset HEAD`

---

## Workflow

**Step 1 — Preflight**
Run `git status --short`, `git diff --cached`, `git diff`, `git diff --stat`.
Stop if: no changes, merge/rebase conflicts, or otherwise uncommittable state.
→ Done when: repo state is fully known.

**Step 2 — Group changes**
Classify every modified file into a logical unit (Rule 3).
→ Done when: every modified file is assigned to exactly one unit.

**Step 3 — Decide staging**
Apply in order:
1. User staged a deliberate subset → commit staged only; leave unstaged.
2. All changes are one logical unit → `git add -A`; single commit.
3. Multiple independent units → `git reset HEAD`; stage and commit per unit.
→ Done when: staging matches the chosen rule.

**Step 4 — Execute commits**
For each unit: choose type/scope/description, stage its files, commit with
the HEREDOC pattern including the `Generated-by` footer. On hook failure,
apply Rule 8.
→ Done when: all planned commits succeed.

**Step 5 — Report**
List commits (hash, title, files). List any uncommitted files with reasons.
Flag anything low-confidence (ambiguous grouping, unclear type) so the user
can review it. Suggest next steps if appropriate (push, open PR).
→ Done when: the user can see what happened and what needs a second look.

---

## References

- `references/commit-message-standard.md` — type selection guide, breaking
  changes, anti-patterns
- `references/examples.md` — grouping and staging scenarios
- `references/debugging-checklist.md` — symptom → fix map when commit
  behavior goes wrong
- `evals/evals.json` — skill evaluation cases
