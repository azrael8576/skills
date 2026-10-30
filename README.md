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
| `git-smart-commit` | `1.0.0` | Git commit orchestrator — groups changes into atomic logical units and writes Conventional Commit messages in English. | [git-smart-commit/README.md](./skills/git-smart-commit/README.md) |
| `github-code-review` | `2.0.0` | Manual GitHub PR review pipeline using gh CLI; posts a validated comment or saves an isolated eval run. Run `/github-code-review <PR URL>`. | [github-code-review/README.md](./skills/github-code-review/README.md) |
| `gitlab-mr-description` | `1.0.0` | Writes a paste-ready GitLab MR title and description in 繁體中文 from the repository template. | [gitlab-mr-description/README.md](./skills/gitlab-mr-description/README.md) |

Install steps, pipelines, and error contracts live in each skill directory's README.
