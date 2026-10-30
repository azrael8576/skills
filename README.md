# Workflow Skills

Reusable agent skills I use daily.

## Install

Interactive install:

```bash
npx skills@latest add azrael8576/skills
```

## Skills

| Name | Version | Description | Docs |
| --- | --- | --- | --- |
| `git-smart-commit` | `1.0.0` | Git commit orchestrator — groups changes into atomic logical units and writes Conventional Commit messages in English. | [SKILL.md](./skills/git-smart-commit/SKILL.md) |
| `github-code-review` | `2.0.0` | Manual GitHub PR review pipeline using gh CLI; posts a validated comment or saves an isolated eval run. Run `/github-code-review <PR URL>`. | [README.md](./skills/github-code-review/README.md) |
| `gitlab-mr-description` | — | Writes a paste-ready GitLab MR title and description in 繁體中文 from the repository template. | [SKILL.md](./skills/gitlab-mr-description/SKILL.md) |

Install steps, pipelines and error contracts live in each skill's own README or SKILL.md.
