# git-smart-commit Debugging Checklist

Map an observed failure to the rule or step that needs re-checking.

## Symptom → Fix

| Symptom | Fix |
|---------|-----|
| Agent skipped preflight | Re-run Workflow Step 1 before anything else |
| Agent used conversation memory instead of git | Re-read `git status`/`git diff`; memory is unreliable |
| Agent ran `git log` | Format is fixed — skip history inspection |
| Wrong commit type | Re-read the diff; check `references/commit-message-standard.md` type table |
| Wrong scope | Scope must match the changed module in the diff |
| Over-split commits | Same feature across files → one commit (Rule 3–4) |
| Under-split commits | Independent purposes → split with `git reset HEAD` (Rule 5) |
| Ignored user staging | Staging Decision Order rule 1 — respect deliberate staging |
| Missing `Generated-by` | Verify the HEREDOC includes the footer |
| Non-English commit message | Entire message must be English regardless of conversation language |
| Used `--no-verify` | Fix the hook's root cause instead; retry without bypass |
| Committed secrets | Stop and warn — never commit `.env`/credentials |
| Fell back to built-in commit behavior | This skill replaces it — re-read from the top |
| Asked user for confirmation before committing | Execute directly; user can amend/reset afterward |

## Pre-commit Hook Failure Loop

1. Read the hook error — identify the file and rule.
2. Fix only what the hook requires (formatting, lint).
3. Re-stage the affected files.
4. Retry `git commit` with the same message.
5. Still failing after 2 attempts → stop, report the error, do not bypass.

## Post-commit Verification

- [ ] `git status` reflects the expected state
- [ ] Every commit has a `Generated-by` footer
- [ ] No secrets committed
- [ ] Uncommitted files documented with reasons
