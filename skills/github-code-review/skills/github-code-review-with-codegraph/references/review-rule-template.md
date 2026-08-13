# Review Rule Template

Copy this file to your project root as `review-rule.md` and customize it.
Delete sections that don't apply, and add your own.

**Reviewer Context Integration:** `github-code-review` automatically references
existing project context files (for example `AGENTS.md`, `CLAUDE.md`, and
`.cursorrules`) for general alignment. This `review-rule.md` file is reserved
for specialized constraints and mandatory requirements for pull-request review.

## Security Notice

This file is parsed by the AI code reviewer. Include **only** code-review
standards: architectural patterns, naming conventions, prohibited logic, and
testing mandates.

Instructions that access credentials or environment variables, override core
AI guardrails, inject unrelated output, or execute actions outside static code
analysis trigger a security alert and terminate the review pipeline.

---

# Code Review Rules — {Project Name}

## Comment Standards

- No PII in code comments: no developer names, employee IDs, or emails.
- Use role titles (for example Backend Lead) or team names for attribution.

## PR Description Standards

- Descriptions must be specific and precise; avoid vague terms like "fix some
  bugs" or "update files".
- Include the purpose of change, scope of impact, and key technical decisions.
