---
name: gitlab-mr-description
description: Write a paste-ready GitLab MR title and description in 繁體中文 from the repository template. Use when asked to write or open an MR (寫 MR、開 MR), including stacked MRs.
---

# GitLab MR description

Produce one paste-ready Markdown file per MR. The reader is a reviewer who did not build the change and opens a large diff cold. Give them three things the diff cannot: the runtime **flow**, the **impact** on existing behavior, and the author's **decisions**. Every sentence is **evidence**-backed fact; the reviewer forms the judgment.

## Workflow

1. **Distill the author context.** Read the conversation and any plan, PRD, issue or spec. Extract the problem, decisions with reasons, constraints, work split and open questions that shaped the final code. Condense them into the author's decisions; keep chat order and wording out. On a cold call, use commits, MR comments and the issue. Mark a material reason with no source as unknown, or ask the author. Done when each decision you keep has a traceable source.

2. **Read the net change.** Run `git log <target>..<source>`, `git diff --stat <target>...<source>` and `git diff -M --name-status <target>...<source>`, then read the diff. Describe head versus target only: a bug introduced and fixed inside the same MR is not a fix, and code added then removed inside the MR stays out of the description. Take behavior from code and intent from author context. Done when each changed file's status is known and each behavior you will describe has a code location.

3. **Trace the flow and the impact.** Follow the changed path from its entry point (user action, route, event, API response or caller) to its visible result, with its important branches. For a refactor, trace one request through the old and new shapes. Then list each shared function, provider or existing page whose behavior changed, and find its callers. Done when each major path runs entry → result and each changed shared behavior names its callers.

4. **Collect template and evidence.** Read `.gitlab/merge_request_templates/*.md`. Collect what actually ran in this session (analyze, tests, manual checks, doc checks). Grep the identifiers in changed docs against the head code; collect each doc or comment that disagrees. Done when each checklist item maps to evidence or to a reason it stays unchecked.

5. **Draft.** Write `MR-<序號或主題>.md` in the scratchpad with the layout below, in `/asd-ste100` STE-flavored Chinese: short active sentences, one fact per sentence, plain team words, exact identifiers and numbers. Done when every flow step, impact line and decision matches the evidence.

6. **Reread as the reviewer.** Read the draft top to bottom as a stranger on GitLab. Check: the first 2 lines say both what and why; one name per concept, each defined at first use; every count matches the items shown; each fact appears in one form only (picture, table or step); the summary and impact fit the first screen. Fix what fails. Done when all five hold.

7. **Validate and report.** For Mermaid, run `npx -y @probelabs/maid@0.0.29 <file>.md`; if it cannot run, say so. Report the absolute file path, the title in a code block, the create-MR URL with target branch when known, the reason for each unchecked box, and any doc conflict the author can fix before merging. Done when the file is ready to paste.

## Layout

First line, the title:

```
<!-- 標題（貼到 Title 欄位）：#<issue> <區域>：<一句話說結果> -->
```

Write the title in product words a PM understands: a place and a result, free of API fields, enum values and class names. Add `[n/N]` after the issue number in a stacked series.

Keep the template's headings, checklist and emoji. Inside 📌 變更摘要, in this order:

1. **摘要**: 1–2 lines. What changed and why, with one concrete fact (a count, a place, a scenario). A source ticket is not a why; state the problem it solves.
2. **影響範圍**: up to 3 lines, directly under 摘要. Each line is a fact: which existing page, shared function or provider behaves differently now, and which callers reach it, for example 「`buildNavigateUrl` 改為先編碼網址。側欄、常用功能、儲值中心都走這個函式。」 State the change and the callers; the reviewer judges the consequence. Omit the section when no existing behavior changed.
3. **圖表**: pictures where structure or time is hard to follow in prose: component data flow, timeline, before/after, small table. One idea per picture, as many pictures as help. Prose and picture complement each other: a linear chain that the flow list already states stays as prose. Label each numbering scheme. In a timeline, name the starting event of each time, including alternate paths.
4. **流程**: numbered steps in runtime order, matching the diagrams. Each step starts with what happens and ends with its symbol, for example 「點擊金幣後，controller 決定開啟方式（`CampaignLauncher`）」。 Use as many steps as the flow needs. Tie each important branch to its observable result, and tell apart an internal failure result, a log and a user-visible message.
5. **設計決策**: up to 5 choices a reviewer would question, each as 「選 X：理由」, adding the rejected alternative when the source names one. Cite only sources the reader can open.
6. **其他變更**: small changes outside the main theme.
7. When applicable: **已知未處理** for limitations, open questions and documents that still disagree with the code at merge time; **閱讀提示** for diff tools or commits to skip (by title); **合併順序** for stacked MRs.
8. **驗證**: one line naming what ran, when something ran. For an unchecked box that could mean "not run" or "failed", state which.

Language: use team words (內開、外開、重試、貼邊) and keep one name per concept. Define a product term or jargon at first use. Write whole Chinese sentences with English only inside code identifiers. When an API name differs from what the user sees on screen, give both. Describe outcomes concretely, including how similar destinations differ. Keep each pronoun next to its subject.

GitLab width (~900px column): prose lines ≤100 display columns (CJK = 2), tables ≤3 columns, Mermaid LR ≤5 nodes or TD ≤10, labels ≤30 columns, ASCII lines ≤72 columns. Use ASCII ids for Mermaid nodes and subgraphs, and keep quotes out of edge labels. Keep content visible; `<details>` holds at most one long reference-only list.

Tick a checklist box only with evidence. End with the template's 🔗 Issue section: `關聯 Issue：Related to #N` by default. Use `修復 Issue：Fixes #N` only when the author confirms this MR completes the issue; an issue that other MRs or commits still reference stays Related. Then:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
