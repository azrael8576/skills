# gitlab-mr-description

Version: `1.0.0`

Writes a paste-ready GitLab MR title and description in 繁體中文 from the
repository's `.gitlab/merge_request_templates/*.md`. Use when asked to write or
open an MR (寫 MR、開 MR), including stacked MRs.

The description gives a reviewer what the diff cannot: the runtime **flow**,
the **impact** on existing behavior, and the author's **decisions**. Every
statement is backed by evidence from the code, commits, or session.

## Output

One Markdown file per MR (`MR-<序號或主題>.md`) in the scratchpad, containing:

- Title line in product words (`#<issue> <區域>：<結果>`)
- 摘要, 影響範圍, 圖表 (Mermaid), 流程, 設計決策, 其他變更, 驗證
- The template's checklist, ticked only with evidence, and the 🔗 Issue section

Mermaid is validated with `npx -y @probelabs/maid@0.0.29 <file>.md`.
Full workflow and layout rules live in
[SKILL.md](./skills/gitlab-mr-description/SKILL.md).

## File Layout

```text
gitlab-mr-description/
├── README.md
└── skills/gitlab-mr-description/
    └── SKILL.md
```
