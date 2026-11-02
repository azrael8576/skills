# git-smart-commit Examples

Extended scenarios for grouping, staging, messaging, and reporting.

---

## Example 1 — Single logical unit, all unstaged

**git status**:
```text
 M src/auth/login.ts
 M src/auth/session.ts
?? src/auth/token.ts
```

All three files implement the same auth token refresh feature → `git add -A`,
one commit:

```text
feat(auth): add token refresh flow

Generated-by: Cursor (Auto)
```

---

## Example 2 — Two independent units, all staged together

**git status**:
```text
A  git-smart-commit/skills/git-smart-commit/SKILL.md
A  prompt-writing-gold-standard-zh/SKILL.md
A  prompt-writing-gold-standard-zh/references/01-gold-standard-principles.md
```

Two independent skills → `git reset HEAD`, stage and commit per group:

```text
chore(git-smart-commit): add commit helper skill

Generated-by: Cursor (Auto)
```

```text
feat(prompt-writing-gold-standard-zh): add Chinese prompt writing skill

Add SKILL.md, reference docs, and eval cases.

Generated-by: Cursor (Auto)
```

---

## Example 3 — Deliberate user staging

**git status**:
```text
M  src/api/handler.ts
 M src/api/middleware.ts
 M src/api/routes.ts
```

Only `handler.ts` is staged (`M ` in first column). Respect user intent —
commit staged only, leave the rest unstaged:

```text
fix(api): correct handler response status code

Generated-by: Cursor (Auto)
```

Report: 1 commit; `middleware.ts` and `routes.ts` left unstaged with reason
"deliberate user staging."

---

## Example 4 — Docs-only change, no body needed

**git status**:
```text
 M README.md
 M docs/setup.md
```

```text
docs: update setup instructions

Generated-by: Cursor (Auto)
```

2 files, clear intent from title — no body triggered.

---

## Example 5 — Large change needing a body

**git status**: 14 files, 1900+ insertions, new skill package.

```text
feat(prompt-writing-gold-standard-zh): add Chinese prompt writing skill

Add complete Chinese localization with SKILL.md, reference docs,
and eval cases for production-grade prompt authoring and review.

Generated-by: Cursor (Auto)
```

Body triggered by file count > 5 and lines > 100.

---

## Example 6 — Pre-commit hook failure

1. `git commit` → hook fails: `eslint: unexpected console.log`
2. Read error → remove or fix `console.log` in flagged file
3. `git add` fixed file
4. `git commit` again with the same message

Never `git commit --no-verify`. On repeated failure after 2 retries: stop and
report the error.

---

## Example 7 — Possible secrets

`git diff` shows `.env` with API keys staged.

**Action**: stop, do not commit, warn the user. Report: "Refusing to commit
`.env` — likely contains secrets."

---

## Example 8 — No changes

`git status` shows a clean working tree.

**Action**: stop, no commit created. Report: "Nothing to commit."

---

## Example 9 — Ambiguous grouping

One file mixes a bug fix and a refactor in the same diff.

**Action**: choose the dominant purpose for `type`. Report it as low-
confidence — e.g. "handler.ts mixes fix and refactor; committed as
`fix(api)` — split it further if that doesn't match your intent."
