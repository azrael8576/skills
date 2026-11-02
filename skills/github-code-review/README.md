# github-code-review

Version: `3.0.0`

Code review for github.com pull requests. `gh` fetches the PR context, a
detached worktree is built at the exact PR head SHA, and the same agent reads
the change file by file with git in the **review phase**, writes the review,
and posts it back to the PR once validation passes. `--eval` runs the same
pipeline but saves every artifact instead of posting.

Only `https://github.com/{owner}/{repo}/pull/{number}` URLs are in scope; any
other host stops at Stage 1 (`prepare`) with `invalid-pr-url`. The pipeline,
prompts and error codes mirror the GitLab skill (`gamania-code-review`); `gh`
replaces its MCP, see
[`error-contract.md`](./skills/github-code-review/references/error-contract.md#mapping-from-the-gitlab-mcp-contract).

## Architecture

| Component | Responsibility |
| --- | --- |
| Skill agent (`SKILL.md`) | Only the two judgment steps: the review rule gate (Stage 3) and the review (Stage 4). Mechanical work goes to the script; the agent never copies the review, brief or API responses. |
| `scripts/review_run.py` | Five calls. `prepare` (Stage 1): parse the PR URL, match `origin` or `upstream` to the PR repository, check `gh` 2.48+, allocate the run. `stage2` (Stage 2): read the PR through `gh api` into seven input files and `execution/materialize-response.json`, build the exact-SHA worktree (fork heads via `refs/pull/<n>/head`) and merge base, check that `git diff` lists GitHub's changed files, best-effort `codegraph init`, render the paths-only review brief. `finalize` (Stage 5): structure check and Review Output Gate; only a pass writes `output/review.md`. `post` (Stage 6, normal only): post that file byte for byte as one PR comment. `finish` (Stage 7): keep eval assets and clean up. Every step lands in `execution/stages.jsonl`. |
| Review phase (`references/review-guide.md`, `references/review-brief.md`) | Stage 4, run by the same agent and model as the pipeline. Reads `git diff BASE_SHA SOURCE_SHA -- <path>` per file and writes only `output/review-draft.md`. |

No review subagent: hosts pick subagent models differently (Kiro falls back
to `auto`), so the whole review stays in one agent. PR content reaches the
review only as artifact paths and the worktree (paths-only). Every documented
`error_type` stops the run; there is no fallback review.

## Requirements

- Git >=2.36, uv, Python >=3.10 (the scripts use only the standard library)
- [GitHub CLI](https://cli.github.com/) >=2.48, logged in: `gh auth login`.
  The comment is posted as the active `gh` account (`gh auth switch` to change it).
- Optional: Codegraph (`npx @colbymchenry/codegraph`); the review uses its
  `codegraph_explore` MCP tool when the host has it, else the `codegraph` CLI

`gh` runs with your token, and an agent's shell can use it too; the skill
only guarantees that the comment it posts passed every gate. To limit what
an agent can do, run it with a [fine-grained token](https://github.com/settings/personal-access-tokens)
scoped to the repository with only *Pull requests: read and write* and
*Contents: read*, e.g. `GH_TOKEN=<token>`.

## Install

```bash
npx skills@latest add azrael8576/skills
```

Or copy `skills/github-code-review/` into your agent's skills directory
(`~/.claude/skills`, `~/.cursor/skills`, `~/.codex/skills`, `~/.kiro/skills`).

Add `review-rule.md` to the PR's **target branch** (e.g. `main`), starting from
[`references/review-rule-template.md`](./skills/github-code-review/references/review-rule-template.md).

## Use

Run from a local clone of the PR repository. In a fork clone, `upstream` may
point at the PR repository instead of `origin`.

```text
/github-code-review https://github.com/your-org/your-repo/pull/123
/github-code-review --eval https://github.com/your-org/your-repo/pull/123
```

## Pipeline

```text
Stage 1  prepare: parse PR URL + check workspace remote + gh + run paths
Stage 2  stage2: materialize PR artifacts through gh + worktree + Codegraph + brief
Stage 3  Security gate on target review rule
Stage 4  Review: follow review-guide, write the draft
Stage 5  finalize: validate review output
Stage 6  Normal: post through review_run.py; eval: save intended comment
Stage 7  finish: keep eval assets, clean temporary files
Final    Show status, Verdict and links
```

Eval runs are kept under `~/cr-evals/github.com/<owner>/<repo>/pr-<number>/`;
see [`references/run-artifacts.md`](./skills/github-code-review/references/run-artifacts.md).

### Codegraph

Codegraph is best effort. `stage2` runs `npx @colbymchenry/codegraph init` in
the worktree:

- Success → `CODEGRAPH_AVAILABLE=true`. The review phase finds related code
  with Codegraph first (`review-guide.md` Context Gathering), and the line
  after the signature must be `used CodeGraph !`; `finalize` checks it.
- Failure, timeout or no `npx` → `CODEGRAPH_AVAILABLE=false`. The pipeline
  continues with Read/grep and the review has no footer.

### Gates

- **Stage 3**: the agent reads the target branch's `review-rule.md` line by
  line against `references/security-gates.md`; anything suspicious stops the
  run with `prompt-injection-detected`.
- **Stage 5**: `finalize` runs the structure check
  (`scripts/validate_review_structure.py`) and the Review Output Gate, and
  confirms Stage 4 wrote nothing but the draft. Only a full pass writes
  `output/review.md`, and only that file is posted.

Review Output Gate rules (implemented in `scripts/review_run.py`; evidence
records only the line number and category, never the matched text):

| Category | Rule |
| --- | --- |
| `private-key` | `-----BEGIN … PRIVATE KEY-----` |
| `certificate`, `ssh-key` | `-----BEGIN CERTIFICATE-----`; `ssh-rsa` / `ssh-ed25519` / `ecdsa-sha2-*` followed by `AAAA` key data |
| `base64` | 80+ base64 characters mixing upper case, lower case and digits that are not in the PR diff |
| `system-file` | a passwd line (`root:x:0:0:`) or a shadow hash (`user:$6$`) |
| `token` | tokens starting with `glpat-`, `ghp_`, `gho_`, `sk-`, or `AKIA` plus 16 characters |
| `env-dump` | 5 or more consecutive `KEY=value` lines |
| `password` | `password` / `passwd` / `密碼` followed by `:`, `：` or `=` and a quoted string of 4+ characters (except `***`) |
| relevance | when there are findings, the review cites at least one changed file's path or name |

## Output

```markdown
### 🤖 AI Code Review — #123

`feature/login` → `main` · @author

新增登入功能，service layer 清楚；網路錯誤尚未處理。

---

#### 錯誤處理

| 嚴重度 | 項目 | 建議 |
|--------|------|------|
| 🔴 | `auth_service.ts:42` 網路錯誤未捕捉，畫面會直接崩潰 | 捕捉錯誤並顯示可重試的提示 |

---

**Verdict: 修正 critical 問題後再 merge**

---
*claude-opus-5-5 · 2026-10-30 17:30 UTC+8*
used CodeGraph !
```

The `used CodeGraph !` line appears only when Codegraph indexed the worktree.

## File Layout

```text
github-code-review/
├── CONTEXT.md
├── README.md
└── skills/github-code-review/
    ├── SKILL.md
    ├── evals/evals.json
    ├── references/
    │   ├── error-contract.md
    │   ├── review-brief.md            # brief template (stage2 renders execution/review-brief.md)
    │   ├── review-guide.md            # review phase procedure
    │   ├── review-rule-template.md
    │   ├── run-artifacts.md           # eval artifact contract
    │   └── security-gates.md
    └── scripts/
        ├── review_run.py              # prepare / stage2 / finalize / post / finish
        └── validate_review_structure.py
```

## Errors

See [`references/error-contract.md`](./skills/github-code-review/references/error-contract.md).

