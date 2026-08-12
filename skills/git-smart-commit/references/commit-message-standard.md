# Conventional Commit Message Standard (extended)

Supports `git-smart-commit` SKILL.md. Core format, types, and body rules
already live there — this file covers what SKILL.md doesn't.

## Type Selection Guide

| Type | When to use | SemVer |
|------|-------------|--------|
| `feat` | New feature or capability | MINOR |
| `fix` | Bug fix | PATCH |
| `refactor` | Restructure without behavior change | — |
| `docs` | Documentation only | — |
| `test` | Add or update tests | — |
| `chore` | Maintenance: deps, CI, configs | — |
| `style` | Formatting, whitespace (no logic change) | — |
| `perf` | Performance improvement | — |
| `ci` | CI/CD configuration | — |
| `build` | Build system or external dependencies | — |

## Breaking Changes

Append `!` after type/scope and add a `BREAKING CHANGE:` footer:

```text
feat(api)!: remove legacy auth endpoint

BREAKING CHANGE: /v1/auth endpoint removed; use /v2/auth instead.

Generated-by: Cursor (claude-4-sonnet)
```

## HEREDOC with Body

```bash
git commit -m "$(cat <<'EOF'
feat(auth): add Chinese localization to prompt-writing skill

Add complete Chinese localization with SKILL.md, reference docs,
and eval cases for production-grade prompt authoring.

Generated-by: Cursor (claude-4-sonnet)
EOF
)"
```

## Anti-patterns

| Bad | Why | Better |
|-----|-----|--------|
| `Updated files` | Not conventional; no type | `chore(deps): update lodash to 4.17.21` |
| `feat: Add feature` | Capitalized description | `feat(ui): add dark mode toggle` |
| `fix: 修復登入問題` | Non-English | `fix(auth): resolve login redirect loop` |
| Missing `Generated-by` | Breaks audit trail | Always include footer |
| `git log` before commit | Unnecessary; format is fixed | Skip `git log` |
