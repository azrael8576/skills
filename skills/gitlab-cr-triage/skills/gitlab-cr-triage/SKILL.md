---
name: gitlab-cr-triage
version: 1.0.0
description: Triage a code review (AI or human) on a GitLab MR, fix only the verified real problems, and draft replies for the rest. Use when the user pastes an MR link with CR comments and asks to handle them (處理 CR、修 CR、看這份 CR).
---

# GitLab CR triage

A CR is a list of **claims**, not a to-do list. Each finding earns a fix only after its claim survives verification against the code, the project's own rule text, and the author's standards below. The author's time goes to real problems; noise gets a short, evidence-backed reply.

Reply to the user in 繁體中文. Keep code identifiers in their original form.

## Workflow

1. **Load the MR.** Run `glab mr view <url>` and `glab mr diff <url>`. Confirm the local branch is the MR source branch and its HEAD matches the MR head SHA; when it differs, say so and work in the session's existing worktree for that branch. Done when you know the MR's diff and which local checkout you will edit.

2. **Split the CR into findings.** One finding per claim: what it says is wrong, the location it cites, the rule it cites, and the fix it suggests. The CR's severity labels and verdict are input, not evidence. Done when every table row and paragraph of the CR maps to a finding.

3. **Verify each finding.** For each claim gather three kinds of evidence:
   - **Code**: open the cited `file:line`; confirm the behavior the CR describes actually happens on the MR head.
   - **Rule**: quote the exact sentence of the cited rule (`AGENTS.md`, `.agents/docs/rules/*`, `CLAUDE.md`). A rule forbids only what its words forbid.
   - **Precedent**: grep the repo for how existing code handles the same situation (e.g. whether any test already asserts this kind of thing, whether other call sites use the suggested shape).

   Done when every finding has a code location, a quoted rule sentence (or "no rule cited"), and a precedent count.

4. **Classify.** Put each finding in exactly one bucket, using the standards below:
   - **真問題**: the code is wrong (a bug, broken behavior, data or security risk), or it breaks a rule sentence as written, or it fails an author standard.
   - **雜訊**: the claim is false, the rule does not say it, or the suggestion conflicts with an author standard.
   - **待作者決定**: the answer depends on a product spec or a trade-off only the author owns.

   Done when every finding has a bucket and a one-line reason that cites its evidence.

5. **Fix the 真問題.** Make the smallest change that resolves each one, following the repo's `AGENTS.md`. Run only the tests and analyzer for the files you touched. Leave commits and MR comments to the author. Done when each fix has a passing test or analyzer run you can show.

6. **Report.** Output three sections, in this order, every finding in exactly one:
   - **已處理**: each 真問題, with what changed (`file:line`) and the test or analyzer output that proves it.
   - **不處理**: each 雜訊, with the reason in one line: either the claim failed verification (cite the evidence), or it conflicts with a named author standard below (name the standard). Follow it with a paste-ready reply for the MR thread: 1–3 sentences in 繁體中文, courteous and final. When a project rule's words do require the thing and an author standard overrides it, the reply says so plainly: a deliberate deviation, the rule quoted, the reason given.
   - **待作者決定**: the exact question and the options.

   Done when the author can see at a glance what was fixed, what was skipped and under which standard, and can paste the replies without rereading the CR.

## Author standards

These are the author's stated positions. A CR suggestion that conflicts with one is 雜訊, even when it sounds like good practice.

- **Tests: high value only.** A test earns its place when it would turn red on a plausible real regression and green only when behavior is correct. A test that restates a constant, mirrors the implementation, or passes regardless of behavior (false green) is noise. When the CR asks for a test type the repo has no precedent for, weigh the value directly: a missing-test finding is 真問題 only for a branch where a real bug could hide unseen.
- **Rule compliance is literal.** "The only place in `lib/` that does this", "deviates from the usual pattern", or "the rule describes X" do not make a violation. A rule that describes the common case and is silent about this case is not broken.
- **UI component APIs carry UI concerns only.** A widget, dialog, or `showXxx` function takes the parameters the UI needs. A parameter added for consistency, tracking, logging, or service access is noise, unless a test genuinely needs that injection point.
- **Reuse beats new shape.** Prefer the API and pattern the surrounding code already uses over a new abstraction the CR proposes.

When the author overrules a bucket during the session with a new reason, propose that reason as a new standard here, worded as the position it states.
