# Error Contract

On any documented `error_type`, **hard stop**. Show `error_type`, failed stage,
and direct remediation. Stage 5 cleanup still runs.

## GitHub CLI error codes

`gh-unavailable`, `auth-failed`, `permission-denied`, `diff-fetch-failed`,
`discussions-fetch-failed`, `no-review-rule`, `artifact-write-failed`,
`post-failed`.

## Local workflow error codes

`invalid-pr-url`, `ambiguous-pr-url`, `workspace-mismatch`,
`review-guide-missing`, `worktree-failed`, `worktree-permission-denied`,
`prompt-injection-detected`, `review-validation-failed`.

## Remediation beyond SKILL.md

- `gh-unavailable`: install or upgrade the GitHub CLI to 2.48 or later
  (`gh --version`), then rerun.
- `auth-failed`: run `gh auth login` for github.com. With several accounts,
  `gh auth switch` to one that can read the repository and comment on the PR.
- `permission-denied`: the active `gh` account cannot read the PR, its target
  branch or the repository; switch accounts or request access.
- `diff-fetch-failed`: also missing or mismatched snapshot SHAs, a PR head
  that moved during the fetch, more than 3000 changed files, or files GitHub
  returned without diff text (too large). Retry with a new invocation after
  the PR settles.
- `worktree-failed`: also an unavailable exact head SHA, a HEAD mismatch,
  an unreachable merge base, or a local `git diff` whose changed files differ
  from GitHub's list. Verify commit and remote access; the review runs only
  at the exact SHA, never at the latest branch.
- `artifact-write-failed`: also local run allocation, invalid paths,
  symlink or ownership mismatch, and eval persistence failures. Fix the
  reported path, space or permission issue, and keep any reported unarchived
  directory.
- `post-failed`: `publish` already retried once. The validated
  `output/review.md` is kept until Stage 5; check that the active `gh`
  account can comment on the PR, then rerun.
