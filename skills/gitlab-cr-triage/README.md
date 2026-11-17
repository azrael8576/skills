# gitlab-cr-triage

Version: `1.0.0`

Triages a code review (AI or human) pasted with a GitLab MR link. Each finding
is verified against the code, the exact rule text, and repo precedent, then
sorted into 真問題 / 雜訊 / 待作者決定. Only 真問題 get fixed; 雜訊 get a
paste-ready 繁體中文 reply. Use when you paste an MR link plus CR comments and
ask to handle them (處理 CR、修 CR).

## Output

- 已處理: each 真問題 with its change and the test or analyzer run
- 不處理: each 雜訊 with the failed claim or the author standard it conflicts
  with, plus a paste-ready reply (a deliberate rule deviation says so)
- 待作者決定: the question and the options

It never commits, pushes, or posts to the MR. Requires `glab`.

The author standards that decide noise (high-value tests only, literal rule
reading, UI APIs carry UI parameters only, reuse over new shape) live in
[SKILL.md](./skills/gitlab-cr-triage/SKILL.md).

## File Layout

```text
gitlab-cr-triage/
├── README.md
└── skills/gitlab-cr-triage/
    ├── SKILL.md
    └── evals/evals.json
```
