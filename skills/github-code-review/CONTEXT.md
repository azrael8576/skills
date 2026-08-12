# github-code-review

Domain language for the GitHub PR review skill, SubAgent, and posting gates.

## Language

**deployment**:
Posting the review to GitHub is a publish step. If any required context,
validation, or security gate fails, do not publish.
_Avoid_: soft post, best-effort publish, draft upload

**paths-only**:
The SubAgent receives only parameters and artifact file paths. Diff,
discussions, review-rule, and description content are never pasted into the
SubAgent prompt.
_Avoid_: inline context, prompt stuffing, pasted artifacts

**hard stop**:
Any documented `error_type` ends the workflow immediately. No fallback,
partial, or local-diff review path exists.
_Avoid_: degraded mode, compatibility fallback, continue anyway

**producer**:
The SubAgent writing review markdown. Format examples live in
`skills/github-code-review/references/review-guide.md`.
_Avoid_: main agent as author of review body

**structure gate**:
Deterministic markdown structure checks. Single source of truth:
`skills/github-code-review/scripts/validate_review_structure.py`.
_Avoid_: duplicating header/verdict/signature rules in SKILL or security-gates

**Review Output Gate**:
Non-structure posting checks in `references/security-gates.md`: secret-leak
patterns and review relevance (changed-file path, with zero-findings exception).
_Avoid_: structure markers inside this gate

**isolated-context subagent**:
A fresh agent session spawned by the IDE/LLM provider's native mechanism with
no shared context from the main agent beyond the explicit prompt. If the
provider cannot spawn such a session, the workflow hard-stops with
`subagent-unavailable`.
_Avoid_: in-context continuation, shared-memory dispatch
