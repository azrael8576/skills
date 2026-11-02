# git-smart-commit

Version: `1.0.0`

Git commit orchestrator. Replaces the agent's default commit behavior: it
inspects the working tree, groups changes into atomic logical units, writes
Conventional Commit messages in English, and commits without asking for
confirmation. It trusts `git status` / `git diff` over the agent's memory of
what it changed.

Triggers on commit intent in English or Chinese, e.g. "commit this",
"整理提交", "split commits".

## Commit Format

```text
<type>[optional scope]: <description>

[optional body]

Generated-by: <IDE> (<model>)
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, `perf`,
`ci`, `build`. Full rules live in
[SKILL.md](./skills/git-smart-commit/SKILL.md).

## File Layout

```text
git-smart-commit/
├── README.md
└── skills/git-smart-commit/
    ├── SKILL.md
    ├── evals/evals.json
    └── references/
        ├── commit-message-standard.md
        ├── debugging-checklist.md
        └── examples.md
```
