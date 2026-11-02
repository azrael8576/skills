# Error Contract

On any documented `error_type`, **hard stop**. Show `error_type`, failed stage,
and direct remediation. Stage 7 cleanup still runs.

## GitHub CLI error codes

`auth-failed`, `permission-denied`, `diff-fetch-failed`,
`discussions-fetch-failed`, `no-review-rule`, `artifact-write-failed`,
`post-failed`.

## Local workflow error codes

`invalid-pr-url`, `ambiguous-pr-url`, `gh-unavailable`,
`workspace-mismatch`, `review-guide-missing`, `worktree-failed`,
`worktree-permission-denied`, `prompt-injection-detected`,
`review-validation-failed`.

## Mapping from the GitLab MCP contract

| GitLab | GitHub |
|---|---|
| `invalid-mr-url`, `ambiguous-mr-url` | `invalid-pr-url`, `ambiguous-pr-url` |
| `mcp-tools-unavailable`, `configuration-error` | `gh-unavailable` (gh has no host/token config; login is `auth-failed`) |
| `confidential-mr` | none: GitHub PRs have no confidential flag |
| `unknown-tool`, `unexpected-error`, `gitlab-api-failed` | none: no MCP tool dispatch, and every `gh api` call names its own error type |
| every other code | same name, same meaning |

## Remediation beyond SKILL.md

- `gh-unavailable`: install or upgrade the GitHub CLI to 2.48 or later
  (`gh --version`), then rerun.
- `auth-failed`: run `gh auth login` for github.com. With several accounts,
  `gh auth switch` to one that can read the repository and comment on the PR.
- `permission-denied`: the active `gh` account cannot read the PR, its target
  branch or the repository; switch accounts or request access.
- `diff-fetch-failed`: also missing, invalid or mismatched snapshot SHAs, a
  PR head that moved during the fetch, more than 3000 changed files, or files
  GitHub returned without diff text (too large). Retry with a new invocation
  after the PR data settles.
- `worktree-failed`: also an unavailable exact source SHA, a HEAD mismatch,
  an unreachable merge base, or a local `git diff` whose changed files differ
  from GitHub's list. Verify commit and remote access; the review runs only
  at the exact SHA, never at the latest branch.
- `artifact-write-failed`: also local run allocation, invalid paths,
  symlink or ownership mismatch, and eval persistence failures. Fix the
  reported path, space or permission issue, and keep any reported unarchived
  directory.
- `post-failed`: `post` already retried once. The validated
  `output/review.md` is kept until Stage 7; check that the active `gh`
  account can comment on the PR, then rerun.
