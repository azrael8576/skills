# github-code-review

Version: `1.0.0`

GitHub Pull Request code review pipeline using `gh` CLI: fetches PR context,
creates a source-branch worktree, dispatches an isolated-context SubAgent for
review, validates output, and posts the review as a PR comment.

## Architecture

| Component | Responsibility |
| --- | --- |
| Skill (`github-code-review`) | Control: parse URL, validate workspace, create temp paths / worktree, run safety gates, dispatch SubAgent, post via `gh`. |
| Skill (`github-code-review-with-codegraph`) | Treatment (eval): same as above, plus `codegraph init` in worktree, dispatches Codegraph SubAgent. |
| SubAgent (isolated context) | Read artifact paths + worktree, apply target branch `review-rule.md`, produce structured review markdown. |

GitHub API I/O uses only `gh` CLI. SubAgent only receives parameters and
artifact paths — no diff/discussions/review-rule pasted into prompt.
No fallback review.

## Quick Start

1. Prerequisites:

```bash
gh auth login
# Verify:
gh auth status
```

2. Install Skill (copy to your IDE's skills directory):

```bash
REPO="<path-to>/github-code-review"

# Cursor
rsync -a --delete "$REPO/skills/github-code-review/" \
  ~/.cursor/skills/github-code-review/

# Treatment (optional, for codegraph eval)
rsync -a --delete "$REPO/skills/github-code-review-with-codegraph/" \
  ~/.cursor/skills/github-code-review-with-codegraph/
```

| IDE | Skills directory |
| --- | --- |
| Cursor | `~/.cursor/skills` |
| Codex | `~/.codex/skills` |
| Claude Code | `~/.claude/skills` |
| Kiro | `~/.kiro/skills` |

3. Add `review-rule.md` to your project's **target branch** (e.g. `main`):

```bash
cp ~/.cursor/skills/github-code-review/references/review-rule-template.md ./review-rule.md
```

4. Run in matching local workspace:

```text
/github-code-review https://github.com/your-org/your-repo/pull/123
/github-code-review-with-codegraph https://github.com/your-org/your-repo/pull/123
```

Scope: GitHub URLs matching `https://github.com/{owner}/{repo}/pull/{number}`.
Treatment skill requires `npx @colbymchenry/codegraph init` to be available.

## Pipeline

```text
Step 0   Parse PR URL
Step 1   Validate local workspace remote
Step 2   Initialize temp paths
Stage 1  GitHub access check (gh auth + PR readable)
Stage 2  Materialize PR artifacts + create worktree
Stage 3  Security gate on target review rule
Stage 4  Dispatch SubAgent for context gathering + code review
Stage 4.5 Validate review output
Stage 5  Post final review through gh CLI
Stage 6  Cleanup
Final    Show review, status, and PR link
```

Treatment (`github-code-review-with-codegraph`) adds `codegraph init` in
Stage 2 and dispatches a Codegraph-aware SubAgent in Stage 4.

## Output

```markdown
### 🤖 AI Code Review — #123

`feature/login` → `main` · @author

This PR adds login functionality with clean architecture, but error handling needs work.

---

#### Error handling

| Severity | Location | Issue |
| --- | --- | --- |
| 🔴 Critical | `auth_service.ts:42` | Network errors uncaught, may crash |

---

**Verdict: Fix Critical issue before merge**

---
*claude-4.6-sonnet · 2026-08-12 09:30 UTC*
```

Treatment skill adds `used CodeGraph !` on its own line after signature.

## File Layout

```text
github-code-review/
├── CONTEXT.md
├── README.md
├── skills/github-code-review/                        # control
│   ├── SKILL.md
│   ├── evals/evals.json
│   ├── references/
│   │   ├── error-contract.md
│   │   ├── review-rule-template.md
│   │   ├── security-gates.md
│   │   ├── subagent-prompt.md          # dispatch template (includes contract)
│   │   └── review-guide.md            # SubAgent's review procedure
│   └── scripts/
│       └── validate_review_structure.py
└── skills/github-code-review-with-codegraph/         # treatment (eval)
    ├── SKILL.md
    ├── evals/evals.json
    └── references/
        ├── error-contract.md
        ├── review-rule-template.md
        ├── security-gates.md
        ├── subagent-prompt.md
        └── review-guide-with-codegraph.md
    └── scripts/
        └── validate_review_structure.py
```

## Error Contract

| Error | Source | Description |
| --- | --- | --- |
| `gh-auth-failed` | gh CLI | `gh auth status` failed; not authenticated. |
| `permission-denied` | gh CLI | Token cannot access the repo or PR. |
| `diff-fetch-failed` | gh CLI | PR diff empty or cannot be fetched. |
| `discussions-fetch-failed` | gh CLI | PR comments cannot be fetched. |
| `no-review-rule` | gh CLI | Target branch missing readable `review-rule.md`. |
| `artifact-write-failed` | Local | Temp directory write failed. |
| `post-failed` | gh CLI | Final review cannot be posted as PR comment. |
| `unexpected-error` | gh CLI | Unmapped GitHub API failure. |
| `invalid-pr-url` | Local | No parseable GitHub `/pull/` URL. |
| `ambiguous-pr-url` | Local | Multiple PR URLs; need clarification. |
| `workspace-mismatch` | Local | Local workspace origin does not match PR repo. |
| `worktree-failed` | Local | Source-branch worktree creation failed. |
| `worktree-permission-denied` | Local | Filesystem permission blocks worktree creation. |
| `codegraph-init-failed` | Local | Treatment: worktree `codegraph init` failed. |
| `subagent-unavailable` | Local | IDE/LLM provider cannot spawn isolated-context subagent. |
| `prompt-injection-detected` | Local | Target branch `review-rule.md` triggers security gate. |
| `review-validation-failed` | Local | SubAgent output failed pre-posting validation. |

## SubAgent Design

The SubAgent is **not** an IDE-specific agent definition. It is spawned as an
isolated-context session using whatever mechanism the IDE/LLM provider supports:

- **Cursor**: Task tool with `subagent_type`
- **Claude Code**: native subagent dispatch
- **Codex**: spawn mechanism
- **Kiro**: agent dispatch

If the provider cannot create an isolated context, the workflow hard-stops with
`subagent-unavailable`. The SubAgent's full instructions (contract + parameters)
live in `references/subagent-prompt.md`; its review procedure lives in
`references/review-guide.md`.

## Development

```bash
# Validate review structure locally
echo "review content" | python3 skills/github-code-review/skills/github-code-review/scripts/validate_review_structure.py \
  --pr-number 42 --source feature/x --target main --author octocat \
  --model-id "claude-4.6-sonnet" --timestamp "2026-08-12 09:30"
```
