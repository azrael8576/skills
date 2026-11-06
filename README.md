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
| `asd-ste100` | `0.4.0` | Simplifies technical English while preserving facts, conditions, and uncertainty. Upstream: [danyuchn/asd-ste100-skill](https://github.com/danyuchn/asd-ste100-skill/tree/fd86652be0d5d3485c69e1529dfd1fa3c9a7a640). | [SKILL.md](./skills/asd-ste100/SKILL.md) |
| `git-smart-commit` | `1.0.0` | Git commit orchestrator — groups changes into atomic logical units and writes Conventional Commit messages in English. | [git-smart-commit/README.md](./skills/git-smart-commit/README.md) |
| `github-code-review` | `3.0.0` | Manual PR review pipeline for github.com using gh CLI; posts a validated comment or saves an isolated eval run. Run `/github-code-review <PR URL>`. | [github-code-review/README.md](./skills/github-code-review/README.md) |
| `gitlab-mr-description` | `1.0.0` | Writes a paste-ready GitLab MR title and description in 繁體中文 from the repository template. | [gitlab-mr-description/README.md](./skills/gitlab-mr-description/README.md) |

Install steps, pipelines, and error contracts live in each skill directory's README.
