# Run files

**Eval only.** Normal mode does not read this file; `SKILL.md` § Modes lists
every difference between the modes. One `review_run.py init` creates one invocation. `run_dir` contains `input/`,
`output/`, `execution/` and `run.json`. `worktree_path` is always under the
private system temp directory. All paths/ref identity are stored in an owner
marker; `finish` refuses traversal, symlinks and mismatched ownership.

Normal mode retains these files only during execution. Eval mode writes to:

```text
~/cr-evals/github.com/<owner>/<repo>/pr-<number>/
  <UTC timestamp>_<ide>_<full UUID>/
    README.md                         # human index, relative links
    run.json                          # schema_version: 1
    input/                            # the seven frozen inputs prepare fetched
    output/review-draft.md             # actual review-phase output, even if rejected
    output/review.md                   # exact intended comment, only if gates pass
    output/validation.json            # original structure validator response
    output/security-gates.json        # both security gate outcomes/evidence
    execution/pr-snapshot.json        # SHAs, artifact paths and PR summary from prepare
    execution/review-brief.md          # paths-only brief rendered by review_run.py
    execution/stages.jsonl             # written by review_run.py
    execution/codegraph.log           # if attempted
    execution/host-trace.jsonl         # only when host exports it
    execution/runtime.json            # only known versions/measurements
    skill/                            # actual skill, references and scripts
    assets/codegraph/                 # generated index, if any
```

`review_run.py` writes one JSON line per stage: `stage`, `started_at`,
`finished_at` (UTC ISO 8601), `status` and structured `error` when failed.
Its own steps use its clock; agent stages end at the write time of
the file they leave (`security-gates.json`,
`review-draft.md`) and start where the previous stage
ended. `prepare` saves the snapshot even when fetching fails. Never continue
review with incomplete inputs.

Stage 2 writes the rule gate to `output/security-gates.json` (format in
SKILL.md); `publish` adds the output gate. Structure status comes from the
validator's unchanged `ok` field. `finish --status completed`
requires all gates passed, a draft, the complete snapshot/seven frozen inputs
and, in normal mode, a posted comment (`publish` records the status);
it never labels a rejected draft as `review.md`. A post failure can still
retain a validated `review.md`.

`run.json` stores identity, UTC times, mode, IDE/model/language, full snapshot
SHAs/project IDs, input fingerprint, versions, gates, terminal status,
failed stage/error, publish status, cleanup outcome and a relative file index
with byte counts/SHA256/purpose. `run.json` and derived `README.md` are not
self-hashed. Missing early snapshots/measurements are explicitly unavailable.
The fingerprint hashes the four commit SHAs plus all seven frozen inputs;
it excludes paths, UUID, IDE, model and time. Compare equal fingerprints when
evaluating different runs. No shared case index, repo clone or backup is made.

If the host provides measurements, put only known values in
`execution/runtime.json`: `versions` (gh/Codegraph version strings) and
`observability` (`tokens`, `cost`, `model_parameters`, each with
`status: available`, `value` and `source`). Otherwise leave them unavailable.
Do not guess model parameters, token usage/cost, hidden reasoning or a host
trace. Never store credentials, environment dumps or secret-bearing logs.

Failure/cancellation: call `finish` with the real status, publish status,
failed stage and error. Eval keeps every observable file already produced.
Archive failure retains the unarchived worktree and reports its path. Normal
cleans its own run even when review failed. Cleanup outcome is separate from
review outcome; show leftovers, never remove another invocation's directory.

Reproduction requires the original repo and exact SHAs from local Git or the
remote. If a commit is no longer obtainable, report failure rather than use
the latest branch. Reuse the archived context; descriptions, discussions and
target-branch rules may have changed since the original run.

## Runtime requirements

Require Git >=2.36 for `fetch --no-write-fetch-head` and
`worktree list --porcelain -z`. The latter preserves unusual path bytes
without quoting ambiguity; see the [Git 2.36 release notes](https://github.com/git/git/blob/v2.36.0/Documentation/RelNotes/2.36.0.txt).
Older Git installations must update before using this version.

Python >=3.10 and uv were already required. The lifecycle helper uses only
the Python standard library; its commands are specified in the main skill.
