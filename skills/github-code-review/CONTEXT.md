# github-code-review

Domain language for the GitHub PR review skill, its review phase, and the
gates that guard posting.

## Language

**deployment**:
Posting the review to GitHub is a publish step. If any required context,
validation, or security gate fails, do not publish.
_Avoid_: soft post, best-effort publish, draft upload

**gh boundary**:
`review_run.py prepare` (read) and `publish` (post) are the only GitHub I/O,
both through `gh api`. It is a workflow rule, not a permission boundary: the
agent's shell can run `gh` with the user's token, so the token's scope is the
real limit. The scripts keep GitHub I/O deterministic and out of the model,
and make the only comment the skill posts one that passed every gate.
_Avoid_: MCP, raw gh commands in SKILL.md, curl fallback, agent-side API calls

**review phase**:
Stage 3, run by the same agent that runs the pipeline (2.0.0 dropped the
review subagent: hosts start subagents on different models, Kiro on `auto`).
It follows `references/review-guide.md` with the bindings of the **review
brief**, reads the change with `git diff BASE_SHA SOURCE_SHA` in the worktree,
and writes only `output/review-draft.md`.
_Avoid_: review subagent, spawn, sub-task, reviewer agent

**review brief**:
`execution/review-brief.md`, rendered by `review_run.py prepare` from
`references/review-brief.md`: the run's parameters, Base/Source SHA and
artifact paths, plus the untrusted-input rules.
_Avoid_: reviewer prompt, subagent prompt

**paths-only**:
PR content (diff, discussions, review-rule, description) stays in artifact
files and the worktree; the review phase reads it there, and it never enters
commands, script arguments or messages.
_Avoid_: inline context, prompt stuffing, pasted artifacts

**hard stop**:
Any documented `error_type` ends the workflow immediately. No fallback,
partial, or local-diff review path exists. Codegraph failures are the one
documented exception — see **codegraph-optional**.
_Avoid_: degraded mode, compatibility fallback, continue anyway

**codegraph-optional**:
Codegraph indexing and lookup are a best-effort enhancement to context
gathering in Stage 1/3.4, never a requirement. `review_run.py prepare`
attempts `codegraph init` in the worktree and returns `codegraph_available`
(`true`/`false`); a failure is not an `error_type` and never hard-stops —
the pipeline continues as a normal review with Read/grep discovery and no
`used CodeGraph !` footer.
_Avoid_: codegraph-init-failed as a hard stop, required tool, blocking
dependency, treating "no Codegraph" as degraded

**producer**:
The review phase writing review markdown. Format examples live in the
skill's `references/review-guide.md`.
_Avoid_: main agent as author of review body

**structure gate**:
Deterministic markdown structure checks. Single source of truth:
`skills/github-code-review/scripts/validate_review_structure.py`. The
`used CodeGraph !` footer check (`--require-codegraph-marker`) is part of
this gate, applied only when `CODEGRAPH_AVAILABLE == true`.
_Avoid_: duplicating header/verdict/signature rules in SKILL or security-gates

**Review Output Gate**:
Non-structure posting checks defined and run by
`scripts/review_run.py publish`: secret-leak patterns and review relevance
(changed-file path or name, with zero-findings exception).
_Avoid_: structure markers inside this gate

**run**:
One independent invocation identified by a full UUID. Owns artifacts,
detached worktree, private source ref and outcome. Same-PR invocations can
coexist; each normal run posts independently and removes its temporary data.

**eval mode**:
The same review and gates, with Stage 4 saving the intended comment instead
of publishing. Retains frozen PR context, exact SHAs, observable execution
assets and checksums; does not retain a Git repo by default.

**input fingerprint**:
Hash of fixed commit SHAs and the seven frozen input files. Equal inputs
match across IDEs/models/run paths. Run identity is separate from case identity.
