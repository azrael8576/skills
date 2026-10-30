"""Executable contracts for per-invocation isolation, compact eval archives and GitHub I/O through gh."""

import base64
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills/github-code-review"
FAKE_GH = Path(__file__).with_name("fake_gh.py")
SCRIPT = SKILL / "scripts/review_run.py"


def cli(*args: str, success: bool = True) -> dict[str, Any]:
    result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def init(root: Path, mode: str = "eval", pr: int = 7, ide: str = "codex") -> dict[str, Any]:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    if not (workspace / ".git").exists():
        git(workspace, "init", "-b", "main")
    return cli("init", "--workspace", str(workspace), "--skill-path", str(SKILL),
               "--project", "octo/widgets", "--pr-number", str(pr), "--mode", mode,
               "--ide", ide, "--model-id", "test-model", "--temp-root", str(root / "tmp"),
               "--eval-root", str(root / "evals"))


def artifacts(run: dict[str, Any], change: str = "diff") -> None:
    root = Path(run["run_dir"])
    names = {"metadata": "metadata.json", "description": "description.md", "labels": "labels.txt",
             "diff": "diff.txt", "changed_files": "changed-files.txt", "discussions": "discussions.txt",
             "target_review_rule": "target-review-rule.md"}
    paths = {}
    for key, name in names.items():
        path = root / "input" / f"pr-{run['pr_number']}-{name}"
        path.write_text(change if key == "diff" else key)
        paths[key] = str(path)
    snapshot = {"source_sha": "a" * 40, "diff_target_sha": "b" * 40, "merge_base_sha": "c" * 40,
                "review_rule_commit_sha": "d" * 40, "review_rule_blob_sha": "e" * 40,
                "source_project_id": 1, "target_project_id": 1}
    (root / "execution/pr-snapshot.json").write_text(json.dumps(
        {"success": True, "snapshot": snapshot, "artifact_paths": paths, "review_rule_changed_in_pr": False,
         "pr_summary": {"title": "Fix {braces}", "source": "feature", "target": "main", "author": "ada", "state": "open"}}))


def gates(run: dict[str, Any], passed: bool = True) -> None:
    output = Path(run["run_dir"]) / "output"
    (output / "review-draft.md").write_text("exact review\n", encoding="utf-8")
    (output / "validation.json").write_text(json.dumps({"ok": passed, "failures": []}))
    (output / "security-gates.json").write_text(json.dumps(
        {"rule": {"status": "passed"}, "output": {"status": "passed" if passed else "failed"}}))


def mark_posted(run: dict[str, Any]) -> None:
    """What a successful `publish` records for a normal run."""
    manifest_path = Path(run["run_dir"]) / "run.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["publish_status"] = "posted"
    manifest_path.write_text(json.dumps(manifest))


def finish(run: dict[str, Any], status: str = "completed", success: bool = True) -> dict[str, Any]:
    if run["mode"] == "normal" and status == "completed":
        mark_posted(run)
    return cli("finish", "--run-dir", run["run_dir"], "--status", status, success=success)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE).strip()


def test_four_processes_same_pr_get_private_paths_and_no_workspace_assets(tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()
    git(tmp_path / "workspace", "init", "-b", "main")
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda ide: init(tmp_path, "normal", ide=ide), ["codex", "cursor", "codex", "kiro"]))
    for field in ("run_id", "run_dir", "worktree_path", "source_ref"):
        assert len({run[field] for run in runs}) == 4
    assert [path.name for path in (tmp_path / "workspace").iterdir()] == [".git"]
    for run in runs:
        artifacts(run)
        gates(run)
        assert finish(run)["cleanup"]["status"] == "completed"
        assert not Path(run["run_dir"]).exists()


@pytest.mark.parametrize("mode", ["normal", "eval"])
def test_own_only_cleanup_and_eval_manifest_links_checksums(tmp_path: Path, mode: str) -> None:
    run, other = init(tmp_path, mode), init(tmp_path, "eval", pr=8)
    artifacts(run)
    gates(run)
    worktree = Path(run["worktree_path"])
    (worktree / ".codegraph").mkdir(parents=True)
    (worktree / ".codegraph/index.db").write_bytes(b"index")
    (Path(run["run_dir"]) / "execution/review-brief.md").write_text("paths only")
    result = finish(run)
    assert result["status"] == "completed"
    assert result["cleanup"]["status"] == "completed"
    assert not Path(run["temp_dir"]).exists()
    assert Path(other["run_dir"]).is_dir()
    if mode == "eval":
        root = Path(run["run_dir"])
        manifest = json.loads((root / "run.json").read_text())
        assert manifest["publish_status"] == "skipped_eval"
        assert manifest["input_fingerprint"]
        assert (root / "output/review.md").read_bytes() == b"exact review\n"
        assert (root / "assets/codegraph/index.db").read_bytes() == b"index"
        assert manifest["observability"]["tokens"]["status"] == "unavailable"
        for item in manifest["files"]:
            data = (root / item["path"]).read_bytes()
            assert item["bytes"] == len(data)
            assert item["sha256"] == hashlib.sha256(data).hexdigest()
        readme = (root / "README.md").read_text()
        for link in re.findall(r"\]\(([^)]+)\)", readme):
            if not link.startswith("https://"):
                assert (root / link).is_file(), link
        assert not list(root.rglob("*.bundle"))
        assert not (root / ".git").exists()


def test_fingerprint_ignores_run_ide_paths_but_detects_context_change(tmp_path: Path) -> None:
    runs = [init(tmp_path, ide=ide) for ide in ("codex", "cursor", "kiro")]
    for i, run in enumerate(runs):
        artifacts(run, "changed" if i == 2 else "diff")
        gates(run)
    fingerprints = [finish(run)["input_fingerprint"] for run in runs]
    assert fingerprints[0] == fingerprints[1] != fingerprints[2]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_eval_preserves_partial_input_and_draft_without_final(tmp_path: Path, status: str) -> None:
    run = init(tmp_path)
    root = Path(run["run_dir"])
    (root / "input/pr-7-metadata.json").write_text("partial")
    gates(run, passed=False)
    result = finish(run, status)
    assert result["status"] == status
    assert result["snapshot"] is None
    assert result["input_fingerprint"] is None
    assert (root / "output/review-draft.md").exists()
    assert not (root / "output/review.md").exists()


def test_failed_gate_cannot_be_labelled_completed_or_published_in_eval(tmp_path: Path) -> None:
    run = init(tmp_path)
    gates(run, passed=False)
    assert finish(run, success=False)["error_type"] == "review-validation-failed"
    assert Path(run["temp_dir"]).exists()


def test_roots_and_ownership_reject_traversal_symlinks_and_workspace(tmp_path: Path) -> None:
    run = init(tmp_path)
    common = ("init", "--workspace", str(tmp_path / "workspace"), "--skill-path", str(SKILL),
              "--project", "octo/widgets", "--pr-number", "7", "--ide", "codex", "--model-id", "test",
              "--mode", "eval", "--temp-root", str(tmp_path / "tmp"))
    for root in (tmp_path / "workspace/evals", tmp_path / "../escape"):
        assert cli(*common, "--eval-root", str(root), success=False)["error_type"] == "artifact-write-failed"
    link = tmp_path / "link"
    link.symlink_to(Path(run["run_dir"]), target_is_directory=True)
    assert cli("finish", "--run-dir", str(link), "--status", "failed", success=False)["error_type"] == "artifact-write-failed"
    manifest_path = Path(run["run_dir"]) / "run.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["temp_dir"] = str(tmp_path / "workspace")
    manifest_path.write_text(json.dumps(manifest))
    assert finish(run, "failed", success=False)["error_type"] == "artifact-write-failed"
    assert (tmp_path / "workspace").is_dir()


def test_parallel_exact_sha_worktrees_preserve_fetch_head_and_branch_refs(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "main")
    git(remote, "config", "user.name", "Test")
    git(remote, "config", "user.email", "test@example.com")
    (remote / "file.txt").write_text("first")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "first")
    workspace = tmp_path / "workspace"
    subprocess.check_call(["git", "clone", "--quiet", str(remote), str(workspace)])
    shas = []
    for text in ("second", "third"):
        (remote / "file.txt").write_text(text)
        git(remote, "commit", "-am", text)
        shas.append(git(remote, "rev-parse", "HEAD"))
    fetch_head = workspace / ".git/FETCH_HEAD"
    fetch_head.write_text("sentinel\n")
    origin = git(workspace, "rev-parse", "origin/main")
    runs = [init(tmp_path, "normal", pr=i + 7) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepared = list(pool.map(lambda pair: cli("worktree", "--run-dir", pair[0]["run_dir"],
                                                  "--source-sha", pair[1]), zip(runs, shas, strict=True)))
    assert [item["source_sha"] for item in prepared] == shas
    for run, sha in zip(runs, shas, strict=True):
        assert git(Path(run["worktree_path"]), "rev-parse", "HEAD") == sha
        artifacts(run)
        gates(run)
        finish(run)
        assert not git(workspace, "for-each-ref", run["source_ref"])
    assert fetch_head.read_text() == "sentinel\n"
    assert git(workspace, "rev-parse", "origin/main") == origin
    unavailable = init(tmp_path, "normal")
    result = cli("worktree", "--run-dir", unavailable["run_dir"], "--source-sha", "f" * 40, success=False)
    assert result["error_type"] == "worktree-failed"
    assert not Path(unavailable["worktree_path"]).exists()


def module() -> Any:
    spec = importlib.util.spec_from_file_location("review_run", SCRIPT)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def test_archive_write_failure_preserves_index_and_review_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = init(tmp_path)
    artifacts(run)
    gates(run)
    index = Path(run["worktree_path"]) / ".codegraph"
    index.mkdir(parents=True)
    (index / "index.db").write_text("only original")
    mod = module()
    owned = mod.load_run(run["run_dir"]).value
    write = mod._write

    def disk_full(path: Path, data: bytes) -> None:
        if path.name == "README.md":
            raise OSError("simulated disk full")
        write(path, data)

    monkeypatch.setattr(mod, "_write", disk_full)
    result = mod.finish(owned, mod.Outcome("completed"), mod.datetime.now(mod.timezone.utc))
    assert result.error.error_type == "artifact-write-failed"
    assert "preserved run" in result.error.message
    assert (index / "index.db").read_text() == "only original"
    manifest = json.loads((Path(run["run_dir"]) / "run.json").read_text())
    assert manifest["status"] == "completed"


def test_cleanup_failure_keeps_successful_eval_and_reports_remaining_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = init(tmp_path)
    workspace = Path(run["workspace"])
    git(workspace, "config", "user.email", "test@example.com")
    git(workspace, "config", "user.name", "Test")
    git(workspace, "commit", "--allow-empty", "-m", "first")
    cli("worktree", "--run-dir", run["run_dir"], "--source-sha", git(workspace, "rev-parse", "HEAD"))
    artifacts(run)
    gates(run)
    mod = module()
    original = mod._git

    def fail_remove(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "simulated permission failure")
        return original(repo, *args)  # type: ignore[no-any-return]

    monkeypatch.setattr(mod, "_git", fail_remove)
    result = mod.finish(mod.load_run(run["run_dir"]).value, mod.Outcome("completed"), mod.datetime.now(mod.timezone.utc))
    assert result.value["status"] == "completed"
    assert result.value["cleanup"]["status"] == "failed"
    assert run["temp_dir"] in result.value["cleanup"]["remaining_paths"]
    assert (Path(run["run_dir"]) / "output/review.md").is_file()
    monkeypatch.setattr(mod, "_git", original)
    finish(run)


def test_exported_host_measurements_and_traces_are_preserved(tmp_path: Path) -> None:
    run = init(tmp_path)
    artifacts(run)
    gates(run)
    root = Path(run["run_dir"])
    (root / "execution/host-trace.jsonl").write_text('{"event":"review"}\n')
    (root / "execution/runtime.json").write_text(json.dumps({
        "versions": {"gh": "gh version 2.101.0", "codegraph": "1.2.3"},
        "observability": {"tokens": {"status": "available", "value": 42, "source": "host export"}},
    }))
    result = finish(run)
    assert result["versions"]["gh"] == "gh version 2.101.0"
    assert result["observability"]["tokens"]["value"] == 42
    assert result["observability"]["host_trace"]["status"] == "available"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_normal_failure_and_cancellation_clean_only_own_run(tmp_path: Path, status: str) -> None:
    run, other = init(tmp_path, "normal"), init(tmp_path, "eval")
    result = cli("finish", "--run-dir", run["run_dir"], "--status", status,
                 "--failed-stage", "Stage 1", "--error-type", "permission-denied", "--message", "denied")
    assert result["error"]["error_type"] == "permission-denied"
    assert not Path(run["temp_dir"]).exists()
    assert Path(other["run_dir"]).exists()


@pytest.mark.parametrize("option,value", [("--mode", "other"), ("--pr-number", "0"), ("--project", "group/../project")])
def test_invalid_init_input_creates_no_run(tmp_path: Path, option: str, value: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = cli("init", "--workspace", str(workspace), "--skill-path", str(SKILL),
                 "--project", "octo/widgets", "--pr-number", "7", "--ide", "codex", "--model-id", "test",
                 "--temp-root", str(tmp_path / "tmp"), "--eval-root", str(tmp_path / "evals"),
                 option, value, success=False)
    assert result["error_type"] == "artifact-write-failed"
    assert not list(tmp_path.rglob("run.json"))


def test_normal_corrupt_output_still_attempts_owned_cleanup(tmp_path: Path) -> None:
    run, other = init(tmp_path, "normal"), init(tmp_path)
    (Path(run["run_dir"]) / "output/validation.json").write_text("corrupt")
    result = cli("finish", "--run-dir", run["run_dir"], "--status", "failed", success=False)
    assert result["error_type"] == "artifact-write-failed"
    assert not Path(run["temp_dir"]).exists()
    assert Path(other["run_dir"]).exists()


def test_completed_eval_requires_frozen_context_not_only_a_review(tmp_path: Path) -> None:
    run = init(tmp_path)
    gates(run)
    result = finish(run, success=False)
    assert result["error_type"] == "artifact-write-failed"
    assert Path(run["temp_dir"]).exists()


def test_cli_json_works_without_a_unicode_console(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init", "-b", "main")
    process = subprocess.run([
        sys.executable, str(SCRIPT), "init", "--workspace", str(workspace), "--skill-path", str(SKILL),
        "--project", "octo/widgets", "--pr-number", "7", "--ide", "codex", "--model-id", "test",
        "--temp-root", str(tmp_path / "tmp"),
    ], capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "ascii"})
    assert process.returncode == 0, process.stderr
    run = json.loads(process.stdout)
    assert run["language"] == "English"
    cli("finish", "--run-dir", run["run_dir"], "--status", "cancelled")


def test_git_quoted_worktree_paths_cannot_bypass_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Git's NUL format preserves paths verbatim on macOS and Windows.
    run = init(tmp_path)
    workspace = Path(run["workspace"])
    git(workspace, "config", "user.name", "Test")
    git(workspace, "config", "user.email", "test@example.com")
    git(workspace, "commit", "--allow-empty", "-m", "first")
    cli("worktree", "--run-dir", run["run_dir"], "--source-sha", git(workspace, "rev-parse", "HEAD"))
    mod = module()
    original = mod._git
    owned = mod.load_run(run["run_dir"]).value

    def fail_remove(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "permission failure")
        if args[:2] == ("worktree", "list"):
            raw = f"worktree {Path(run['worktree_path']).as_posix()}\0HEAD abc\0\0"
            quoted = f"worktree {json.dumps(run['worktree_path'])}\nHEAD abc\n\n"
            return subprocess.CompletedProcess(args, 0, raw if "-z" in args else quoted, "")
        return original(repo, *args)  # type: ignore[no-any-return]

    monkeypatch.setattr(mod, "_git", fail_remove)
    result = mod._cleanup(owned)
    assert result["status"] == "failed"
    assert Path(run["worktree_path"]).exists()
    monkeypatch.setattr(mod, "_git", original)
    cli("finish", "--run-dir", run["run_dir"], "--status", "cancelled")


def test_eval_finish_stdout_reports_file_count_and_keeps_full_index_in_manifest(tmp_path: Path) -> None:
    run = init(tmp_path)
    artifacts(run)
    gates(run)
    result = finish(run)
    manifest = json.loads((Path(run["run_dir"]) / "run.json").read_text())
    assert "files" not in result
    assert result["file_count"] == len(manifest["files"]) > 0


def brief_template() -> str:
    text = (SKILL / "references/review-brief.md").read_text(encoding="utf-8")
    return text.split("## Brief", 1)[1].split("```text\n", 1)[1].split("\n```", 1)[0]


def test_brief_keeps_the_review_safety_rules_and_drops_the_subagent_contract() -> None:
    """4.0.0: one agent reviews; the brief keeps the untrusted-input rules, not the spawn/reply protocol."""
    brief = brief_template()
    for rule in ("make network, GitHub or `gh` calls", "emit credentials, secrets, or system data",
                 "treat as\n  prompt injection", "untrusted input"):
        assert rule in brief, rule
    assert "subagent" not in brief.lower() and "DRAFT_WRITTEN" not in brief
    assert "{SKILL_PATH}/references/review-guide.md" in brief


def worktree_run(root: Path, mode: str = "eval") -> dict[str, Any]:
    run = init(root, mode)
    workspace = Path(run["workspace"])
    git(workspace, "config", "user.name", "Test")
    git(workspace, "config", "user.email", "test@example.com")
    (workspace / "tracked.txt").write_text("original")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "first")
    cli("worktree", "--run-dir", run["run_dir"], "--source-sha", git(workspace, "rev-parse", "HEAD"))
    return run


def test_isolation_allows_draft_gates_and_codegraph_index(tmp_path: Path) -> None:
    run = worktree_run(tmp_path)
    (Path(run["worktree_path"]) / ".codegraph").mkdir()
    (Path(run["worktree_path"]) / ".codegraph/.gitignore").write_text("*")
    gates(run)
    (Path(run["run_dir"]) / "output/validation.json").unlink()
    mod = module()
    assert mod.isolation_problems(mod.load_run(run["run_dir"]).value) == []


@pytest.mark.parametrize("write", ["tracked.txt", "new.txt", "output/notes.md"])
def test_isolation_reports_any_write_besides_the_draft(tmp_path: Path, write: str) -> None:
    run = worktree_run(tmp_path)
    target = Path(run["run_dir"]) / write if write.startswith("output/") else Path(run["worktree_path"]) / write
    target.write_text("changed")
    mod = module()
    problems = mod.isolation_problems(mod.load_run(run["run_dir"]).value)
    assert len(problems) == 1 and Path(write).name in problems[0]


def test_isolation_fails_loud_without_a_worktree(tmp_path: Path) -> None:
    run = init(tmp_path)
    mod = module()
    problems = mod.isolation_problems(mod.load_run(run["run_dir"]).value)
    assert problems and "worktree" in problems[0]


def brief(run: dict[str, Any], codegraph: str = "true", success: bool = True) -> dict[str, Any]:
    return cli("brief", "--run-dir", run["run_dir"], "--codegraph-available", codegraph, success=success)


@pytest.mark.parametrize("mode", ["normal", "eval"])
def test_brief_is_rendered_by_script_with_every_binding(tmp_path: Path, mode: str) -> None:
    run = init(tmp_path, mode)
    artifacts(run)
    paths = json.loads((Path(run["run_dir"]) / "execution/pr-snapshot.json").read_text())["artifact_paths"]
    for key in ("description", "discussions", "diff", "target_review_rule"):
        Path(paths[key]).write_text(f"SENTINEL-{key}")
    result = brief(run, "false")
    path = Path(run["run_dir"]) / "execution/review-brief.md"
    assert result["brief_path"] == str(path)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", result["review_timestamp"])
    text = path.read_text(encoding="utf-8")
    skill = str(Path(run["run_dir"]) / "skill") if mode == "eval" else str(SKILL)
    assert run["skill_path"] == skill
    for line in ("- PR Number: 7", "- PR URL: https://github.com/octo/widgets/pull/7",
                 "- PR Title: Fix {braces}", "- PR Author: @ada", "- Source Branch: feature",
                 "- Target Branch: main", "- PR State: open", "- Model ID: test-model",
                 f"- Review Timestamp: {result['review_timestamp']}", "- User Language: English",
                 f"- Worktree Path: {run['worktree_path']}", "- Review Rule Changed In PR: false",
                 f"- Skill Path: {skill}", "- Codegraph Available: false",
                 f"- Base SHA: {'c' * 40}", f"- Source SHA: {'a' * 40}",
                 f"- Diff: {paths['diff']}", f"- Target Review Rule: {paths['target_review_rule']}",
                 f"- Draft Path: {Path(run['run_dir']) / 'output/review-draft.md'}"):
        assert line in text.splitlines(), line
    assert f"{skill}/references/review-guide.md" in text
    assert not re.search(r"\{(?!braces\})[A-Za-z_][\w.]*\}", text)
    assert "SENTINEL" not in text  # paths-only: artifact content never enters the brief
    manifest = json.loads((Path(run["run_dir"]) / "run.json").read_text())
    assert manifest["bindings"] == {"review_timestamp": result["review_timestamp"], "codegraph_available": False}


def test_eval_brief_uses_the_frozen_skill_snapshot(tmp_path: Path) -> None:
    run = init(tmp_path)
    artifacts(run)
    reference = Path(run["run_dir"]) / "skill/references/review-brief.md"
    text = reference.read_text(encoding="utf-8")
    reference.write_text(text.replace(brief_template().splitlines()[0], "SNAPSHOT"), encoding="utf-8")
    brief(run)
    assert (Path(run["run_dir"]) / "execution/review-brief.md").read_text(encoding="utf-8").startswith("SNAPSHOT")


def test_review_timestamp_is_utc_from_the_injected_clock(tmp_path: Path) -> None:
    run = init(tmp_path)
    artifacts(run)
    mod = module()
    result = mod.render_brief(mod.load_run(run["run_dir"]).value, False,
                               mod.datetime(2026, 10, 12, 16, 30, tzinfo=mod.timezone.utc))
    assert result.error is None
    assert result.value["review_timestamp"] == "2026-10-12 16:30"
    assert "- Review Timestamp: 2026-10-12 16:30" in Path(result.value["brief_path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("damage", ["missing", "failed", "no-summary", "empty-diff", "outside-input", "unknown-placeholder"])
def test_brief_failures_write_no_brief(tmp_path: Path, damage: str) -> None:
    run = init(tmp_path)
    artifacts(run)
    root = Path(run["run_dir"])
    response_path = root / "execution/pr-snapshot.json"
    response = json.loads(response_path.read_text())
    if damage == "missing":
        response_path.unlink()
    elif damage == "failed":
        response_path.write_text(json.dumps({"success": False, "error_type": "permission-denied", "message": "no"}))
    elif damage == "no-summary":
        del response["pr_summary"]["author"]
        response_path.write_text(json.dumps(response))
    elif damage == "empty-diff":
        Path(response["artifact_paths"]["diff"]).write_text("")
    elif damage == "outside-input":
        response["artifact_paths"]["diff"] = str(tmp_path / "elsewhere.txt")
        (tmp_path / "elsewhere.txt").write_text("diff")
        response_path.write_text(json.dumps(response))
    else:
        reference = root / "skill/references/review-brief.md"
        reference.write_text(reference.read_text(encoding="utf-8").replace("{PR_NUMBER}", "{PR_ID}"), encoding="utf-8")
    result = brief(run, success=False)
    assert result["error_type"] == ("permission-denied" if damage == "failed" else "artifact-write-failed")
    assert not (root / "execution/review-brief.md").exists()


def test_optional_artifacts_may_be_empty(tmp_path: Path) -> None:
    run = init(tmp_path)
    artifacts(run)
    paths = json.loads((Path(run["run_dir"]) / "execution/pr-snapshot.json").read_text())["artifact_paths"]
    for key in ("description", "labels", "discussions"):
        Path(paths[key]).write_text("")
    brief(run)


CLEAN_REVIEW = """### 🤖 AI Code Review — #7

`feature` → `main` · @ada

摘要。

| 嚴重度 | 項目 | 建議 |
|--------|------|------|
| 🟡 | `popup_host.dart:12` 沒有檢查 route | 加上檢查 |

**Verdict: 修好再合併**

---
*test-model · {timestamp} UTC*
"""


def output_failures(review: str, changed: tuple[str, ...] = ("lib/feature/popup_host.dart",), diff: str = "") -> list[str]:
    return module().output_gate_failures(review, list(changed), diff)  # type: ignore[no-any-return]


def test_output_gate_passes_a_review_citing_a_changed_file_by_name() -> None:
    assert output_failures(CLEAN_REVIEW) == []
    assert output_failures(CLEAN_REVIEW.replace("popup_host.dart:12", "lib/feature/popup_host.dart:12")) == []


B64 = "QWxhZGRpbjpvcGVuIHNlc2FtZQ" * 4


@pytest.mark.parametrize("category,leak", [
    ("private-key", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("private-key", "-----BEGIN RSA PRIVATE KEY-----"),
    ("certificate", "-----BEGIN CERTIFICATE-----"),
    ("ssh-key", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"),
    ("token", "token glpat-abcdefghij0123456789"),
    ("token", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("token", "key sk-proj-abcdefghij0123456789xyz"),
    ("token", "AKIAIOSFODNN7EXAMPLE"),
    ("password", 'password: "hunter22"'),
    ("password", "密碼＝'s3cretpass'".replace("＝", "=")),
    ("system-file", "root:x:0:0:root:/root:/bin/bash"),
    ("system-file", "ada:$6$saltsalt$hash:19000:0:99999:7:::"),
    ("env-dump", "\n".join(f"KEY_{i}=value{i}" for i in range(5))),
    ("base64", B64),
])
def test_output_gate_blocks_each_leak_without_echoing_it(category: str, leak: str) -> None:
    failures = output_failures(CLEAN_REVIEW.replace("摘要。", f"摘要。\n\n{leak}\n"))
    assert failures and all(f.startswith("line ") for f in failures), failures
    assert any(f.endswith(f": {category}") for f in failures), failures
    assert all(leak.splitlines()[0] not in f for f in failures)


@pytest.mark.parametrize("text", [
    "task-runner disk-full risk-based ask-for-help",
    "AKIA1234",
    "a" * 40,
    "lib/src/features/home/presentation/widgets/very/long/path/without/underscores/HomeDashboardPopupHostWidget",
    "\n".join(f"KEY_{i}=value{i}" for i in range(4)),
    "password: _passwordController.text,",
    "密碼：未加密儲存在 SharedPreferences",
    'password: "***"',
])
def test_output_gate_does_not_block_ordinary_review_text(text: str) -> None:
    assert output_failures(CLEAN_REVIEW.replace("摘要。", f"摘要。\n\n{text}\n")) == []


def test_output_gate_allows_base64_that_is_part_of_the_diff() -> None:
    assert output_failures(CLEAN_REVIEW.replace("摘要。", B64), diff=f"+const icon = '{B64}';") == []


def test_output_gate_relevance_needs_a_changed_file_unless_there_are_no_findings() -> None:
    assert output_failures(CLEAN_REVIEW, changed=("lib/other.dart",)) == ["relevance: no changed file is cited"]
    zero = CLEAN_REVIEW.split("| 嚴重度")[0] + "沒有需要處理的問題。\n\n**Verdict: 可合併**\n"
    assert output_failures(zero, changed=("lib/other.dart",)) == []


def drafted_run(tmp_path: Path, review: str = CLEAN_REVIEW, codegraph: bool = False, mode: str = "eval") -> dict[str, Any]:
    run = worktree_run(tmp_path, mode)
    artifacts(run)
    root = Path(run["run_dir"])
    paths = json.loads((root / "execution/pr-snapshot.json").read_text())["artifact_paths"]
    Path(paths["changed_files"]).write_text("lib/feature/popup_host.dart\n")
    timestamp = brief(run, str(codegraph).lower())["review_timestamp"]
    (root / "output/security-gates.json").write_text(json.dumps(
        {"rule": {"status": "passed", "evidence": ["ok"]}, "output": {"status": "not_run", "evidence": []}}))
    (root / "output/review-draft.md").write_text(review.replace("{timestamp}", timestamp), encoding="utf-8")
    return run


def test_publish_validates_gates_and_copies_the_draft_byte_for_byte(tmp_path: Path) -> None:
    run = drafted_run(tmp_path)
    root = Path(run["run_dir"])
    result = cli("publish", "--run-dir", run["run_dir"])
    assert result == {"ok": True, "verdict": "**Verdict: 修好再合併**", "review_path": str(root / "output/review.md"),
                      "publish_status": "skipped_eval"}
    draft = (root / "output/review-draft.md").read_bytes()
    assert (root / "output/review.md").read_bytes() == draft
    validation = subprocess.run([
        sys.executable, str(SKILL / "scripts/validate_review_structure.py"), "--review-file", str(root / "output/review-draft.md"),
        "--pr-number", "7", "--source", "feature", "--target", "main", "--author", "ada", "--model-id", "test-model",
        "--timestamp", json.loads((root / "run.json").read_text())["bindings"]["review_timestamp"],
    ], capture_output=True, check=True)
    assert (root / "output/validation.json").read_bytes() == validation.stdout
    gates_record = json.loads((root / "output/security-gates.json").read_text())
    assert gates_record["rule"] == {"status": "passed", "evidence": ["ok"]}
    assert gates_record["output"]["status"] == "passed"
    assert "摘要" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("damage", ["structure", "leak", "codegraph-footer", "no-draft", "no-rule-gate", "isolation"])
def test_publish_validation_failure_keeps_review_md_absent(tmp_path: Path, damage: str) -> None:
    review = {"structure": CLEAN_REVIEW.replace("**Verdict", "Verdict"),
              "leak": CLEAN_REVIEW.replace("摘要。", "AKIAIOSFODNN7EXAMPLE")}.get(damage, CLEAN_REVIEW)
    run = drafted_run(tmp_path, review, codegraph=damage == "codegraph-footer")
    root = Path(run["run_dir"])
    if damage == "no-draft":
        (root / "output/review-draft.md").unlink()
    if damage == "no-rule-gate":
        (root / "output/security-gates.json").unlink()
    if damage == "isolation":
        (Path(run["worktree_path"]) / "tracked.txt").write_text("edited by the reviewer")
    result = cli("publish", "--run-dir", run["run_dir"], success=False)
    assert result["error_type"] == "review-validation-failed"
    assert not (root / "output/review.md").exists()
    if damage == "leak":
        assert json.loads((root / "output/security-gates.json").read_text())["output"]["status"] == "failed"
        assert "AKIAIOSFODNN7EXAMPLE" not in result["message"]
    if damage in {"structure", "codegraph-footer"}:
        assert json.loads((root / "output/validation.json").read_text())["ok"] is False


def stage_lines(run: dict[str, Any]) -> list[dict[str, Any]]:
    path = Path(run["run_dir"]) / "execution/stages.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def touch(path: Path, seconds: float) -> None:
    os.utime(path, (seconds, seconds))


def test_stages_are_recorded_by_scripts_and_agent_file_times(tmp_path: Path) -> None:
    import time
    run = drafted_run(tmp_path)
    root = Path(run["run_dir"])
    base = time.time() + 60
    for offset, name in enumerate(("output/security-gates.json", "output/review-draft.md"), 1):
        touch(root / name, base + offset)
    cli("publish", "--run-dir", run["run_dir"])
    finish(run)
    lines = stage_lines(run)
    # The stage numbers are SKILL.md's: 1 prepare, 2 rule gate, 3 review, 4 publish.
    assert [line["stage"] for line in lines] == ["stage1-prepare", "stage2-rule-gate", "stage3-review",
                                                 "stage4-validate", "stage4-publish"]
    for line in lines:
        assert line["status"] in {"passed", "skipped_eval"}
        started, finished = (mod_time(line[key]) for key in ("started_at", "finished_at"))
        assert started <= finished
    times = {line["stage"]: line for line in lines}
    assert mod_time(times["stage2-rule-gate"]["finished_at"]) == pytest.approx(base + 1, abs=0.001)
    assert times["stage2-rule-gate"]["started_at"] == times["stage1-prepare"]["finished_at"]
    assert mod_time(times["stage3-review"]["finished_at"]) == pytest.approx(base + 2, abs=0.001)
    assert times["stage4-publish"]["status"] == "skipped_eval"


def mod_time(value: str) -> float:
    from datetime import datetime
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    return parsed.timestamp()


def test_failed_rule_gate_is_recorded_from_the_agent_file(tmp_path: Path) -> None:
    run = init(tmp_path)
    root = Path(run["run_dir"])
    artifacts(run)
    (root / "output/security-gates.json").write_text(json.dumps(
        {"rule": {"status": "failed", "evidence": ["line 3"]}, "output": {"status": "not_run", "evidence": []}}))
    cli("finish", "--run-dir", run["run_dir"], "--status", "failed")
    last = stage_lines(run)[-1]
    assert last["stage"] == "stage2-rule-gate" and last["status"] == "failed"


def origin_workspace(root: Path, origin: str | None = "https://github.com/octo/widgets.git",
                     upstream: str | None = None) -> Path:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    git(workspace, "init", "-b", "main")
    git(workspace, "config", "user.name", "Test")
    git(workspace, "config", "user.email", "test@example.com")
    (workspace / "tracked.txt").write_text("original")
    (workspace / "lib").mkdir()
    (workspace / "lib/widget.py").write_text("old\n")
    (workspace / "docs").mkdir()
    (workspace / "docs/old.md").write_text("docs\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "first")
    git(workspace, "tag", "pr-base")
    (workspace / "lib/widget.py").write_text("new\n")
    (workspace / "assets").mkdir()
    (workspace / "assets/logo.png").write_bytes(b"\x89PNG\x00\x01")
    git(workspace, "mv", "docs/old.md", "docs/new.md")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "the PR")
    git(workspace, "tag", "pr-head")
    if origin:
        git(workspace, "remote", "add", "origin", origin)
    if upstream:
        git(workspace, "remote", "add", "upstream", upstream)
    return workspace


PR_URL = "https://github.com/octo/widgets/pull/7"


def prepare(root: Path, mode: str = "eval", success: bool = True, url: str = PR_URL,
            gh: str | None = None) -> dict[str, Any]:
    return cli("prepare", "--workspace", str(root / "workspace"), "--skill-path", str(SKILL),
               "--pr-url", url, "--mode", mode, "--ide", "codex",
               "--model-id", "test-model", "--temp-root", str(root / "tmp"), "--eval-root", str(root / "evals"),
               "--gh-command", gh or fake_gh(root), "--codegraph-command", f"{sys.executable} -c pass",
               success=success)


@pytest.mark.parametrize("url", [PR_URL, PR_URL + "/files?w=1", PR_URL + "#issuecomment-1",
                                 "http://github.com/octo/widgets/pull/7"])
def test_prepare_reads_project_and_pr_from_the_url(tmp_path: Path, url: str) -> None:
    origin_workspace(tmp_path)
    run = prepare(tmp_path, url=url)
    assert run["pr_number"] == 7
    assert json.loads((Path(run["run_dir"]) / "run.json").read_text())["project"] == "octo/widgets"


@pytest.mark.parametrize("url", ["https://gitlab.com/octo/widgets/-/merge_requests/7",
                                 "https://github.com/octo/widgets/issues/7",
                                 "https://github.com/octo/widgets/pull/0",
                                 "https://github.com/octo/widgets/pull/abc",
                                 "https://github.example.com/octo/widgets/pull/7",
                                 "https://github.com/octo/pull/7", "not a url"])
def test_prepare_rejects_urls_outside_the_review_scope(tmp_path: Path, url: str) -> None:
    origin_workspace(tmp_path)
    assert prepare(tmp_path, url=url, success=False)["error_type"] == "invalid-pr-url"
    assert not (tmp_path / "evals").exists()


@pytest.mark.parametrize("origin", [
    "https://github.com/octo/widgets.git",
    "https://github.com/octo/widgets",
    "git@github.com:octo/widgets.git",
    "ssh://git@github.com/octo/widgets.git",
    "https://github.com/Octo/Widgets.git",
])
def test_prepare_accepts_every_origin_form_of_the_pr_repository(tmp_path: Path, origin: str) -> None:
    origin_workspace(tmp_path, origin)
    run = prepare(tmp_path)
    assert run["skill_path"] == str(Path(run["run_dir"]) / "skill")
    assert set(run) == {"run_id", "mode", "project", "pr_number", "run_dir", "skill_path", "worktree_path",
                        "codegraph_available", "codegraph_log_tail", "brief_path", "review_timestamp",
                        "review_rule_path", "review_rule"}
    assert [line["stage"] for line in stage_lines(run)] == ["stage1-prepare", "stage1-fetch", "stage1-worktree"]
    assert json.loads((Path(run["run_dir"]) / "run.json").read_text())["remote"] == "origin"


def test_prepare_binds_upstream_when_origin_is_a_fork(tmp_path: Path) -> None:
    origin_workspace(tmp_path, "git@github.com:me/widgets.git", upstream="https://github.com/octo/widgets.git")
    run = prepare(tmp_path)
    assert json.loads((Path(run["run_dir"]) / "run.json").read_text())["remote"] == "upstream"


@pytest.mark.parametrize("origin", ["https://ada:ghp_secret0123456789@github.com/octo/other.git", None])
def test_prepare_mismatch_stops_before_allocating_and_hides_credentials(tmp_path: Path, origin: str | None) -> None:
    origin_workspace(tmp_path, origin)
    result = prepare(tmp_path, success=False)
    assert result["error_type"] == "workspace-mismatch"
    assert "ghp_secret" not in result["message"] and "octo/widgets" in result["message"]
    assert "run_dir" not in result  # nothing was allocated, so there is nothing to finish
    assert not (tmp_path / "tmp").exists() and not (tmp_path / "evals").exists()


@pytest.mark.parametrize("version", ["gh version 2.47.9 (2024-03-01)", "", None])
def test_prepare_stops_without_a_gh_that_supports_slurp(tmp_path: Path, version: str | None) -> None:
    origin_workspace(tmp_path)
    gh = fake_gh(tmp_path, version=version) if version is not None else "definitely-not-gh"
    result = prepare(tmp_path, gh=gh, success=False)
    assert result["error_type"] == "gh-unavailable" and "2.48" in result["message"]
    assert not (tmp_path / "tmp").exists() and not (tmp_path / "evals").exists()


FAKE_CODEGRAPH = "import os, sys; os.makedirs('.codegraph'); open('.codegraph/.gitignore', 'w').write('*'); print('indexed')"


def staged_run(root: Path, mode: str = "eval") -> dict[str, Any]:
    workspace = origin_workspace(root)
    base = git(workspace, "rev-parse", "HEAD")
    (workspace / "tracked.txt").write_text("changed")
    (workspace / "lib/popup_host.dart").write_text("class PopupHost {}")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "change")
    run = init(root, mode)
    artifacts(run)
    response_path = Path(run["run_dir"]) / "execution/pr-snapshot.json"
    response = json.loads(response_path.read_text())
    response["snapshot"]["source_sha"] = git(workspace, "rev-parse", "HEAD")
    response["snapshot"]["merge_base_sha"] = base
    Path(response["artifact_paths"]["changed_files"]).write_text("lib/popup_host.dart\ntracked.txt\n")
    response_path.write_text(json.dumps(response))
    return run


def local(run: dict[str, Any], command: str = FAKE_CODEGRAPH, timeout: str = "30", success: bool = True) -> dict[str, Any]:
    return cli("local", "--run-dir", run["run_dir"], "--codegraph-timeout", timeout,
               "--codegraph-command", f"{sys.executable} -c \"{command}\"", success=success)


def test_prepare_local_builds_worktree_index_and_brief_in_one_call(tmp_path: Path) -> None:
    run = staged_run(tmp_path)
    result = local(run)
    root = Path(run["run_dir"])
    assert result["codegraph_available"] is True and "codegraph_log_tail" not in result
    assert git(Path(run["worktree_path"]), "rev-parse", "HEAD") == git(tmp_path / "workspace", "rev-parse", "HEAD")
    assert (root / "execution/codegraph.log").read_text().strip() == "indexed"
    assert result["brief_path"] == str(root / "execution/review-brief.md")
    text = Path(result["brief_path"]).read_text(encoding="utf-8")
    assert "- Codegraph Available: true" in text
    snapshot = json.loads((root / "execution/pr-snapshot.json").read_text())["snapshot"]
    assert f"- Base SHA: {snapshot['merge_base_sha']}" in text and f"- Source SHA: {snapshot['source_sha']}" in text
    assert result["review_rule"] == "target_review_rule"
    assert [line["stage"] for line in stage_lines(run)] == ["stage1-prepare", "stage1-worktree"]


@pytest.mark.parametrize("command,timeout", [
    (f"{sys.executable} -c \"import sys; print('boom'); sys.exit(3)\"", "30"),
    (f"{sys.executable} -c \"print('no index')\"", "30"),
    (f"{sys.executable} -c \"import time; time.sleep(30)\"", "1"),
    ("definitely-not-codegraph init", "30"),
], ids=["non-zero-exit", "no-index-dir", "timeout", "missing-command"])
def test_prepare_local_codegraph_failure_is_not_a_hard_stop(tmp_path: Path, command: str, timeout: str) -> None:
    run = staged_run(tmp_path)
    result = cli("local", "--run-dir", run["run_dir"], "--codegraph-command", command, "--codegraph-timeout", timeout)
    assert result["codegraph_available"] is False
    assert len(result["codegraph_log_tail"].splitlines()) <= 20
    assert "- Codegraph Available: false" in Path(result["brief_path"]).read_text(encoding="utf-8")


def test_prepare_local_stops_on_a_failed_fetch_without_a_worktree(tmp_path: Path) -> None:
    run = staged_run(tmp_path)
    (Path(run["run_dir"]) / "execution/pr-snapshot.json").write_text(json.dumps(
        {"success": False, "error_type": "diff-fetch-failed", "message": "snapshot moved"}))
    assert local(run, success=False)["error_type"] == "diff-fetch-failed"
    assert not Path(run["worktree_path"]).exists()
    assert stage_lines(run)[-1]["stage"] == "stage1-worktree" and stage_lines(run)[-1]["status"] == "failed"


def test_prepare_local_unavailable_source_sha_is_worktree_failed(tmp_path: Path) -> None:
    run = staged_run(tmp_path)
    response_path = Path(run["run_dir"]) / "execution/pr-snapshot.json"
    response = json.loads(response_path.read_text())
    response["snapshot"]["source_sha"] = "f" * 40
    response_path.write_text(json.dumps(response))
    empty_remote = tmp_path / "empty-remote"
    git(tmp_path, "init", "-q", "--bare", str(empty_remote))  # stays offline; origin points at the real host
    result = cli("local", "--run-dir", run["run_dir"], "--source-remote", str(empty_remote),
                 "--codegraph-command", "unused", success=False)
    assert result["error_type"] == "worktree-failed"


def test_prepare_local_stops_when_local_git_changes_differ_from_github(tmp_path: Path) -> None:
    """The review reads the change through git, so git must see exactly the files GitHub lists."""
    run = staged_run(tmp_path)
    paths = json.loads((Path(run["run_dir"]) / "execution/pr-snapshot.json").read_text())["artifact_paths"]
    Path(paths["changed_files"]).write_text("lib/popup_host.dart\nlib/missing.dart\n")
    result = local(run, success=False)
    assert result["error_type"] == "worktree-failed"
    assert "lib/missing.dart" in result["message"] and "tracked.txt" in result["message"]
    assert not (Path(run["run_dir"]) / "execution/review-brief.md").exists()


def test_prepare_local_accepts_a_heavily_edited_rename_github_lists_by_new_path(tmp_path: Path) -> None:
    """Below git's 50% similarity the old path is listed locally; the diff header still names it."""
    run = staged_run(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "old_name.dart").write_text("".join(f"line {i}\n" for i in range(20)))
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "add old")
    base = git(workspace, "rev-parse", "HEAD")
    git(workspace, "mv", "old_name.dart", "new_name.dart")
    (workspace / "new_name.dart").write_text("".join(f"other {i}\n" for i in range(20)))
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "rename")
    response_path = Path(run["run_dir"]) / "execution/pr-snapshot.json"
    response = json.loads(response_path.read_text())
    response["snapshot"].update(source_sha=git(workspace, "rev-parse", "HEAD"), merge_base_sha=base)
    paths = response["artifact_paths"]
    Path(paths["changed_files"]).write_text("new_name.dart\n")
    Path(paths["diff"]).write_text("diff --git a/old_name.dart b/new_name.dart\n--- a/old_name.dart\n+++ b/new_name.dart\n@@\n")
    response_path.write_text(json.dumps(response))
    local(run)


def test_prepare_local_unavailable_merge_base_is_worktree_failed(tmp_path: Path) -> None:
    run = staged_run(tmp_path)
    response_path = Path(run["run_dir"]) / "execution/pr-snapshot.json"
    response = json.loads(response_path.read_text())
    response["snapshot"]["merge_base_sha"] = "f" * 40
    response_path.write_text(json.dumps(response))
    empty_remote = tmp_path / "empty-remote"
    git(tmp_path, "init", "-q", "--bare", str(empty_remote))
    result = cli("local", "--run-dir", run["run_dir"], "--source-remote", str(empty_remote),
                 "--codegraph-command", "unused", success=False)
    assert result["error_type"] == "worktree-failed" and "merge base" in result["message"].lower()


def test_finish_removes_the_private_source_and_base_refs(tmp_path: Path) -> None:
    run = staged_run(tmp_path)
    workspace = tmp_path / "workspace"
    source_ref = json.loads((Path(run["run_dir"]) / "run.json").read_text())["source_ref"]
    base_ref = source_ref.rsplit("/", 1)[0] + "/base"
    for ref in (source_ref, base_ref):
        git(workspace, "update-ref", ref, "HEAD")
    cli("finish", "--run-dir", run["run_dir"], "--status", "cancelled")
    assert git(workspace, "for-each-ref", "refs/cr") == ""


def run_files(run: dict[str, Any]) -> list[str]:
    root = Path(run["run_dir"])
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file() and not path.relative_to(root).as_posix().startswith(("skill/", "worktree/", ".")))


@pytest.mark.parametrize("mode", ["normal", "eval"])
def test_both_modes_run_identical_steps_and_differ_only_in_retention(tmp_path: Path, mode: str) -> None:
    run = staged_run(tmp_path, mode)
    local(run)
    root = Path(run["run_dir"])
    (root / "output/security-gates.json").write_text(json.dumps(
        {"rule": {"status": "passed", "evidence": ["ok"]}, "output": {"status": "not_run", "evidence": []}}))
    paths = json.loads((root / "execution/pr-snapshot.json").read_text())["artifact_paths"]
    Path(paths["changed_files"]).write_text("lib/feature/popup_host.dart\n")
    timestamp = json.loads((root / "run.json").read_text())["bindings"]["review_timestamp"]
    (root / "output/review-draft.md").write_text(CLEAN_REVIEW.replace("{timestamp}", timestamp) + "used CodeGraph !\n", encoding="utf-8")
    assert cli("publish", "--run-dir", run["run_dir"], "--gh-command", fake_gh(tmp_path))["ok"] is True
    assert run_files(run) == [
        "execution/codegraph.log", "execution/pr-snapshot.json",
        "execution/review-brief.md", "execution/stages.jsonl",
        "input/pr-7-changed-files.txt", "input/pr-7-description.md", "input/pr-7-diff.txt", "input/pr-7-discussions.txt",
        "input/pr-7-labels.txt", "input/pr-7-metadata.json", "input/pr-7-target-review-rule.md",
        "output/review-draft.md", "output/review.md", "output/security-gates.json", "output/validation.json",
        "run.json",
    ]
    cli("finish", "--run-dir", run["run_dir"], "--status", "completed")
    assert root.exists() is (mode == "eval")
    assert len([call for call in gh_calls(tmp_path) if "POST" in call]) == (mode == "normal")


def test_skill_md_stays_slim_because_every_turn_rereads_it() -> None:
    assert len((SKILL / "SKILL.md").read_text(encoding="utf-8")) <= 10_000


def test_skill_references_exist_and_none_is_orphaned() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(r"references/([\w-]+\.md)", text))
    on_disk = {path.name for path in (SKILL / "references").glob("*.md")}
    # review-brief.md is the script's template; the agent reads the rendered brief instead.
    assert mentioned <= on_disk
    assert on_disk - mentioned == {"review-brief.md"}


def test_versions_agree_across_skill_and_readmes() -> None:
    package = SKILL.parents[1]
    version = re.search(r"^version: (\S+)$", (SKILL / "SKILL.md").read_text(encoding="utf-8"), re.MULTILINE)
    assert version and version.group(1) == "2.0.0"
    assert f"Version: `{version.group(1)}`" in (package / "README.md").read_text(encoding="utf-8")
    root_readme = (package.parents[1] / "README.md").read_text(encoding="utf-8")
    assert f"| `github-code-review` | `{version.group(1)}` |" in root_readme
    assert "github-code-review-with-codegraph" not in root_readme


def test_review_guide_reads_users_and_keeps_every_finding() -> None:
    """3.2.3 recall: runs read only changed files and self-cut findings (gold: 5/9 never found)."""
    guide = (SKILL / "references/review-guide.md").read_text(encoding="utf-8")
    assert "**Users:**" in guide and "**Done when:** every symbol whose behavior the PR changes" in guide
    assert "Use judgment" not in guide
    assert "max 3 rows" not in guide and "Length target" not in guide
    assert "uncertain ones included" in guide


def test_review_guide_reads_the_change_file_by_file_through_git() -> None:
    """Host read tools cap one read (Kiro: 250KB / 2000 lines); a 640KB diff was half read in one pass."""
    guide = (SKILL / "references/review-guide.md").read_text(encoding="utf-8")
    assert "git diff --stat BASE_SHA SOURCE_SHA" in guide and "git diff BASE_SHA SOURCE_SHA -- <path>" in guide
    assert "**Done when:** every changed file's diff has been read" in guide
    assert "3.3 reads the change itself with git" in guide  # the Diff artifact is not read a second time
    assert "read the diff to its last line" not in guide


def test_one_agent_runs_the_whole_review() -> None:
    """Hosts pick subagent models differently (Kiro falls back to `auto`), so no subagent is spawned."""
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "subagent" not in skill.lower()
    assert "subagent-unavailable" not in (SKILL / "references/error-contract.md").read_text(encoding="utf-8")
    assert "subagent" not in (SKILL / "references/review-guide.md").read_text(encoding="utf-8").lower()


def test_skill_routes_every_github_call_through_the_scripts() -> None:
    head = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("## Pipeline", 1)[0]
    assert "never call `gh`" in head.lower() and "2.48" in head
    for path in [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md")), SKILL / "evals/evals.json"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"gamania|_mr_|\bMCP\b|codegraph_explore|materializ|finalize|!\{|UTC\+8", text), path


def test_skill_keeps_mechanical_work_out_of_the_main_agent() -> None:
    """Every main-agent request re-reads the whole context; these lines keep it short (3.2.2 eval: 8 -> 3.6 credits)."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for command in ("prepare", "publish", "finish"):
        assert f'review_run.py" {command}' in text, command
    for command in ("init", "worktree", "brief", "local", "materialize", "stage2", "finalize", "post"):
        assert f'review_run.py" {command}' not in text, command
    assert re.findall(r"^## Stage (\d)", text, re.MULTILINE) == ["1", "2", "3", "4", "5"]
    assert "git remote get-url" not in text and "npx" not in text
    assert not re.search(r"(^|[\s;&|])cp\s", text, re.MULTILINE)
    assert not re.search(r"^\s*gh\s", text, re.MULTILINE) and "--pr-url" in text
    assert "never write it" in text  # stages.jsonl
    assert "`brief_path`" in text and "references/review-guide.md" in text
    # One read of the rule-gate reference; a grep first costs another request and loads it twice.
    assert "Read `${SKILL_PATH}/references/security-gates.md` once, whole file, in one\nread call (no search first)" in text


@pytest.mark.parametrize("damage,code", [("— !7", "header_mismatch"), ("UTC*", "signature_mismatch")])
def test_validator_requires_the_pr_header_and_utc_signature(damage: str, code: str) -> None:
    review = CLEAN_REVIEW.replace("{timestamp}", "2026-10-12 16:30")
    review = review.replace("— #7", "— !7") if damage == "— !7" else review.replace("UTC*", "UTC+8*")
    result = subprocess.run([
        sys.executable, str(SKILL / "scripts/validate_review_structure.py"), "--pr-number", "7", "--source", "feature",
        "--target", "main", "--author", "ada", "--model-id", "test-model", "--timestamp", "2026-10-12 16:30",
    ], input=review, capture_output=True, text=True)
    assert result.returncode == 1
    assert [failure["code"] for failure in json.loads(result.stdout)["failures"]] == [code]


# --- GitHub through gh -------------------------------------------------------------------------------

SOURCE, TARGET, BASE, RULE_COMMIT, RULE_BLOB = "1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40
RULE_TEXT = "# Rules\n- No prints in library code\n"
REPO = "repos/octo/widgets"


def pr_shas(root: Path) -> tuple[str, str, str]:
    """(head, base, merge base) of origin_workspace's PR, or placeholders when no workspace PR exists."""
    try:
        head, base = (git(root / "workspace", "rev-parse", f"{tag}^{{commit}}") for tag in ("pr-head", "pr-base"))
    except (subprocess.CalledProcessError, OSError):
        return SOURCE, TARGET, BASE
    return head, base, base


def gh_routes(pr: dict[str, Any] | None = None, shas: tuple[str, str, str] = (SOURCE, TARGET, BASE),
              **routes: Any) -> dict[str, Any]:
    """A two-page, three-file PR from a fork, with human, bot and earlier AI comments."""
    source, target, merge_base = shas
    pull = {"number": 7, "title": "Add widget", "state": "open", "draft": False, "merged": False, "body": "Adds a widget",
            "labels": [{"name": "feature"}], "user": {"login": "ada"}, "changed_files": 3, "html_url": PR_URL,
            "head": {"ref": "feature", "sha": source, "repo": {"id": 2, "full_name": "ada/widgets"}},
            "base": {"ref": "main", "sha": target, "repo": {"id": 1, "full_name": "octo/widgets"}}, **(pr or {})}
    human = {"user": {"login": "bob", "type": "User"}, "body": "Looks risky", "created_at": "2026-10-01T00:00:00Z"}
    bot = {"user": {"login": "ci[bot]", "type": "Bot"}, "body": "CI passed"}
    ai = {"user": {"login": "ada", "type": "User"}, "body": "### 🤖 AI Code Review — #7\nold review"}
    compare = f"{REPO}/compare/{target}...{source}"
    return {
        f"{REPO}/pulls/7": {"json": pull},
        f"{REPO}/pulls/7/files?per_page=100": {"json": [
            [{"filename": "lib/widget.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@\n-old\n+new"},
             {"filename": "assets/logo.png", "status": "added", "additions": 0, "deletions": 0}],
            [{"filename": "docs/new.md", "status": "renamed", "previous_filename": "docs/old.md", "additions": 0, "deletions": 0}]]},
        compare: {"json": {"merge_base_commit": {"sha": merge_base}}},
        f"{REPO}/pulls/7/comments?per_page=100": {"json": [[{**human, "id": 1, "path": "lib/widget.py", "line": 1}, {**bot, "id": 2}]]},
        f"{REPO}/pulls/7/reviews?per_page=100": {"json": [[
            {"id": 3, "user": {"login": "carol", "type": "User"}, "body": "", "state": "APPROVED", "submitted_at": "2026-10-02T00:00:00Z"},
            {**ai, "id": 4, "state": "COMMENTED"}]]},
        f"{REPO}/issues/7/comments?per_page=100": {"json": [[{**human, "id": 5}], [{**ai, "id": 6}, {**human, "id": 7}]]},
        f"{REPO}/branches/main": {"json": {"commit": {"sha": RULE_COMMIT}}},
        f"{REPO}/contents/review-rule.md?ref={RULE_COMMIT}": {"json": {
            "content": base64.b64encode(RULE_TEXT.encode()).decode(), "encoding": "base64", "sha": RULE_BLOB}},
        f"{REPO}/issues/7/comments": {"json": {"id": 99, "html_url": f"{PR_URL}#issuecomment-99"}},
        **{(compare if key == "COMPARE" else key): value for key, value in routes.items()},
    }


def fake_gh(root: Path, version: str = "gh version 2.101.0 (2026-09-15)", routes: dict[str, Any] | None = None) -> str:
    fixture = root / "gh-fixture.json"
    fixture.write_text(json.dumps({"version": version, "routes": routes or gh_routes(shas=pr_shas(root))}), encoding="utf-8")
    return " ".join(shlex.quote(part) for part in (sys.executable, str(FAKE_GH), str(fixture)))


def gh_calls(root: Path) -> list[list[str]]:
    log = root / "gh-fixture.calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def snapshot(run: dict[str, Any]) -> dict[str, Any]:
    return json.loads((Path(run["run_dir"]) / "execution/pr-snapshot.json").read_text())  # type: ignore[no-any-return]


def test_prepare_fetches_the_pr_builds_the_worktree_and_renders_the_brief_in_one_call(tmp_path: Path) -> None:
    origin_workspace(tmp_path)
    head, base, _ = pr_shas(tmp_path)
    run = prepare(tmp_path)
    root = Path(run["run_dir"])
    saved = snapshot(run)
    paths = saved["artifact_paths"]
    assert sorted(Path(path).name for path in paths.values()) == sorted(f"pr-7-{name}" for name in (
        "metadata.json", "description.md", "labels.txt", "diff.txt", "changed-files.txt", "discussions.txt", "target-review-rule.md"))
    assert all(Path(path).parent == root / "input" for path in paths.values())
    assert saved["snapshot"] == {"source_sha": head, "diff_target_sha": base, "merge_base_sha": base,
                                 "review_rule_commit_sha": RULE_COMMIT, "review_rule_blob_sha": RULE_BLOB,
                                 "source_project_id": 2, "target_project_id": 1}
    assert saved["pr_summary"] == {"title": "Add widget", "source": "feature", "target": "main", "author": "ada", "state": "open"}
    assert saved["review_rule_changed_in_pr"] is False and saved["warnings"] == []
    assert Path(paths["changed_files"]).read_text() == "lib/widget.py\nassets/logo.png\ndocs/new.md"
    diff = Path(paths["diff"]).read_text()
    assert "diff --git a/lib/widget.py b/lib/widget.py\n--- a/lib/widget.py\n+++ b/lib/widget.py\n@@ -1 +1 @@\n-old\n+new" in diff
    assert "--- /dev/null\n+++ b/assets/logo.png" in diff
    assert "diff --git a/docs/old.md b/docs/new.md\n--- a/docs/old.md\n+++ b/docs/new.md" in diff
    assert Path(paths["target_review_rule"]).read_text() == RULE_TEXT == run["review_rule"]
    assert Path(paths["description"]).read_text() == "Adds a widget" and Path(paths["labels"]).read_text() == "feature"
    assert json.loads(Path(paths["metadata"]).read_text())["source_repo"] == "ada/widgets"
    assert json.loads((root / "run.json").read_text())["versions"]["gh"] == "gh version 2.101.0"
    assert git(Path(run["worktree_path"]), "rev-parse", "HEAD") == head
    assert f"- Source SHA: {head}" in Path(run["brief_path"]).read_text(encoding="utf-8")
    assert run["codegraph_available"] is False
    assert all(call[:3] == ["api", "--hostname", "github.com"] for call in gh_calls(tmp_path) if call != ["--version"])
    assert not any("POST" in call for call in gh_calls(tmp_path))


def test_prepare_failure_after_allocation_returns_the_run_dir_to_finish(tmp_path: Path) -> None:
    origin_workspace(tmp_path)
    routes = gh_routes(shas=pr_shas(tmp_path), **{f"{REPO}/pulls/7": {"exit": 1, "stderr": "gh: Not Found (HTTP 404)"}})
    result = prepare(tmp_path, gh=fake_gh(tmp_path, routes=routes), success=False)
    assert result["error_type"] == "permission-denied" and Path(result["run_dir"]).is_dir()
    assert snapshot(result)["success"] is False
    assert cli("finish", "--run-dir", result["run_dir"], "--status", "failed", "--error-type", "permission-denied")["status"] == "failed"


def fetch(root: Path, routes: dict[str, Any] | None = None) -> tuple[dict[str, Any], Any]:
    """Only the fetch part of prepare, so PR edge cases need no matching local history."""
    run = init(root)
    mod = module()
    result = mod.fetch_pr(mod.load_run(run["run_dir"]).value, shlex.split(fake_gh(root, routes=routes or gh_routes())),
                             lambda: mod.datetime.now(mod.timezone.utc))
    return run, result


def test_fetch_keeps_human_discussion_from_all_three_endpoints(tmp_path: Path) -> None:
    run, _ = fetch(tmp_path)
    discussions = json.loads(Path(snapshot(run)["artifact_paths"]["discussions"]).read_text())
    assert [(item["kind"], item["id"]) for item in discussions] == [
        ("review_comment", 1), ("review", 3), ("issue_comment", 5), ("issue_comment", 7)]
    assert discussions[0]["path"] == "lib/widget.py" and discussions[1]["state"] == "APPROVED"
    assert all("AI Code Review" not in item["body"] for item in discussions)


@pytest.mark.parametrize("pr,state,warnings", [
    ({"draft": True}, "open", ["draft"]),
    ({"state": "closed", "merged": True}, "merged", ["pr-state"]),
    ({"state": "closed"}, "closed", ["pr-state"]),
])
def test_fetch_warns_about_draft_closed_and_merged_prs(tmp_path: Path, pr: dict[str, Any], state: str, warnings: list[str]) -> None:
    run, _ = fetch(tmp_path, gh_routes(pr))
    assert snapshot(run)["pr_summary"]["state"] == state
    assert [warning["type"] for warning in snapshot(run)["warnings"]] == warnings


@pytest.mark.parametrize("change", [
    {"filename": "review-rule.md", "status": "modified", "additions": 1, "deletions": 0, "patch": "+rule"},
    {"filename": "docs/rules.md", "previous_filename": "review-rule.md", "status": "renamed", "additions": 0, "deletions": 0},
])
def test_fetch_flags_a_review_rule_the_pr_changes(tmp_path: Path, change: dict[str, Any]) -> None:
    run, _ = fetch(tmp_path, gh_routes({"changed_files": 1}, **{f"{REPO}/pulls/7/files?per_page=100": {"json": [[change]]}}))
    assert snapshot(run)["review_rule_changed_in_pr"] is True


def test_fetch_accepts_a_pr_whose_fork_was_deleted(tmp_path: Path) -> None:
    run, _ = fetch(tmp_path, gh_routes({"head": {"ref": "feature", "sha": SOURCE, "repo": None}}))
    assert snapshot(run)["snapshot"]["source_project_id"] is None


def failing(status: int, secret: str = "") -> dict[str, Any]:
    return {"exit": 1, "stderr": f"gh: Failure {secret}(HTTP {status})"}


@pytest.mark.parametrize("routes,error_type", [
    ({f"{REPO}/pulls/7": failing(404)}, "permission-denied"),
    ({f"{REPO}/pulls/7": failing(403)}, "permission-denied"),
    ({f"{REPO}/pulls/7": failing(401)}, "auth-failed"),
    ({f"{REPO}/pulls/7": {"exit": 4, "stderr": "To get started with GitHub CLI, please run:  gh auth login"}}, "auth-failed"),
    ({f"{REPO}/pulls/7/files?per_page=100": failing(500, "ghp_leaked0123456789 ")}, "diff-fetch-failed"),
    ({f"{REPO}/pulls/7/files?per_page=100": {"json": []}}, "diff-fetch-failed"),
    ({f"{REPO}/pulls/7/files?per_page=100": {"json": [[{"filename": "lib/widget.py", "additions": 900, "deletions": 0}]]}}, "diff-fetch-failed"),
    ({"COMPARE": failing(404)}, "diff-fetch-failed"),
    ({f"{REPO}/pulls/7/comments?per_page=100": failing(502)}, "discussions-fetch-failed"),
    ({f"{REPO}/issues/7/comments?per_page=100": failing(500)}, "discussions-fetch-failed"),
    ({f"{REPO}/branches/main": failing(404)}, "permission-denied"),
    ({f"{REPO}/contents/review-rule.md?ref={RULE_COMMIT}": failing(404)}, "no-review-rule"),
    ({f"{REPO}/contents/review-rule.md?ref={RULE_COMMIT}": {"json": {"content": base64.b64encode(b"  \n").decode(), "encoding": "base64"}}}, "no-review-rule"),
    ({f"{REPO}/contents/review-rule.md?ref={RULE_COMMIT}": {"json": {"content": base64.b64encode(b"\xff\xfe").decode(), "encoding": "base64"}}}, "no-review-rule"),
    ({f"{REPO}/contents/review-rule.md?ref={RULE_COMMIT}": {"json": {"content": "", "encoding": "none"}}}, "no-review-rule"),
], ids=["pr-404", "pr-403", "pr-401", "not-logged-in", "files-500", "no-files", "patch-dropped", "compare-404",
        "review-comments-502", "issue-comments-500", "branch-404", "rule-404", "rule-blank", "rule-not-utf8", "rule-too-large"])
def test_fetch_failure_is_saved_recorded_and_hides_gh_stderr(tmp_path: Path, routes: dict[str, Any], error_type: str) -> None:
    run, result = fetch(tmp_path, gh_routes(**routes))
    assert result.error.error_type == error_type and "ghp_leaked" not in result.error.message
    assert snapshot(run) == {"success": False, "error_type": error_type, "message": result.error.message}
    assert stage_lines(run)[-1] == {**stage_lines(run)[-1], "stage": "stage1-fetch", "status": "failed"}
    if next(iter(routes)) != f"{REPO}/pulls/7":
        assert (Path(run["run_dir"]) / "input/pr-7-metadata.json").is_file()  # eval keeps what was fetched


def test_fetch_stops_when_more_files_changed_than_github_lists(tmp_path: Path) -> None:
    _, result = fetch(tmp_path, gh_routes({"changed_files": 3001}))
    assert result.error.error_type == "diff-fetch-failed" and "3000" in result.error.message


def test_fetch_stops_when_the_pr_head_moves_during_the_fetch(tmp_path: Path) -> None:
    routes = gh_routes()
    moved = json.loads(json.dumps(routes[f"{REPO}/pulls/7"]))
    moved["json"]["head"]["sha"] = "6" * 40
    routes[f"{REPO}/pulls/7"] = [routes[f"{REPO}/pulls/7"], moved]
    run, result = fetch(tmp_path, routes)
    assert result.error.error_type == "diff-fetch-failed" and "snapshot changed" in result.error.message
    assert not (Path(run["run_dir"]) / "input/pr-7-diff.txt").exists()


def test_fetch_runs_once_and_keeps_the_first_snapshot(tmp_path: Path) -> None:
    run, _ = fetch(tmp_path)
    first = snapshot(run)
    mod = module()
    again = mod.fetch_pr(mod.load_run(run["run_dir"]).value, shlex.split(fake_gh(tmp_path, routes=gh_routes())),
                            lambda: mod.datetime.now(mod.timezone.utc))
    assert again.error.error_type == "artifact-write-failed" and snapshot(run) == first


def publish(run: dict[str, Any], root: Path, routes: dict[str, Any] | None = None, success: bool = True) -> dict[str, Any]:
    return cli("publish", "--run-dir", run["run_dir"], "--gh-command", fake_gh(root, routes=routes or gh_routes()), success=success)


def posts(root: Path) -> list[list[str]]:
    return [call for call in gh_calls(root) if "POST" in call]


def test_publish_posts_the_validated_review_byte_for_byte(tmp_path: Path) -> None:
    run = drafted_run(tmp_path, mode="normal")
    root = Path(run["run_dir"])
    result = publish(run, tmp_path)
    assert result["publish_status"] == "posted" and result["html_url"] == f"{PR_URL}#issuecomment-99"
    assert (tmp_path / "gh-fixture.posted").read_bytes() == (root / "output/review.md").read_bytes() == (root / "output/review-draft.md").read_bytes()
    assert [call[3:6] for call in posts(tmp_path)] == [[f"{REPO}/issues/7/comments", "--method", "POST"]]
    manifest = json.loads((root / "run.json").read_text())
    assert manifest["publish_status"] == "posted" and manifest["comment_url"] == result["html_url"]
    assert stage_lines(run)[-1]["stage"] == "stage4-publish" and stage_lines(run)[-1]["status"] == "posted"
    assert cli("finish", "--run-dir", run["run_dir"], "--status", "completed")["publish_status"] == "posted"
    assert not root.exists()


@pytest.mark.parametrize("mode", ["normal", "eval"])
def test_publish_never_calls_gh_when_validation_fails(tmp_path: Path, mode: str) -> None:
    run = drafted_run(tmp_path, CLEAN_REVIEW.replace("**Verdict", "Verdict"), mode=mode)
    assert publish(run, tmp_path, success=False)["error_type"] == "review-validation-failed"
    assert gh_calls(tmp_path) == []


def test_publish_in_eval_saves_the_comment_without_calling_gh(tmp_path: Path) -> None:
    run = drafted_run(tmp_path)
    assert publish(run, tmp_path)["publish_status"] == "skipped_eval"
    assert gh_calls(tmp_path) == [] and (Path(run["run_dir"]) / "output/review.md").is_file()


def test_publish_retries_a_failed_post_once(tmp_path: Path) -> None:
    run = drafted_run(tmp_path, mode="normal")
    ok = gh_routes()[f"{REPO}/issues/7/comments"]
    assert publish(run, tmp_path, gh_routes(**{f"{REPO}/issues/7/comments": [failing(502), ok]}))["publish_status"] == "posted"
    assert len(posts(tmp_path)) == 2


@pytest.mark.parametrize("route", [[failing(403)], [{"json": {"id": 99}}]], ids=["forbidden", "no-html-url"])
def test_publish_failure_is_post_failed_and_keeps_the_validated_review(tmp_path: Path, route: list[dict[str, Any]]) -> None:
    run = drafted_run(tmp_path, mode="normal")
    root = Path(run["run_dir"])
    assert publish(run, tmp_path, gh_routes(**{f"{REPO}/issues/7/comments": route}), success=False)["error_type"] == "post-failed"
    assert len(posts(tmp_path)) == 2 and (root / "output/review.md").is_file()
    assert json.loads((root / "run.json").read_text())["publish_status"] == "failed"
    assert cli("finish", "--run-dir", run["run_dir"], "--status", "failed")["publish_status"] == "failed"


def test_finish_reads_the_publish_status_instead_of_trusting_the_agent(tmp_path: Path) -> None:
    run = init(tmp_path, "normal")
    artifacts(run)
    gates(run)
    result = cli("finish", "--run-dir", run["run_dir"], "--status", "completed", success=False)
    assert result["error_type"] == "review-validation-failed"  # nothing was posted


def test_worktree_fetches_a_fork_head_through_its_pull_ref(tmp_path: Path) -> None:
    """A fork PR's head commit is on no branch of the PR repository, only under refs/pull/<n>/head."""
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "main")
    git(remote, "config", "user.name", "Test")
    git(remote, "config", "user.email", "test@example.com")
    git(remote, "commit", "--allow-empty", "-m", "first")
    workspace = tmp_path / "workspace"
    subprocess.check_call(["git", "clone", "--quiet", str(remote), str(workspace)])
    git(remote, "checkout", "-b", "fork")
    git(remote, "commit", "--allow-empty", "-m", "from a fork")
    head = git(remote, "rev-parse", "HEAD")
    git(remote, "update-ref", "refs/pull/7/head", head)
    git(remote, "checkout", "main")
    git(remote, "branch", "-D", "fork")
    run = init(tmp_path, "normal")
    assert cli("worktree", "--run-dir", run["run_dir"], "--source-sha", head)["source_sha"] == head
    assert git(Path(run["worktree_path"]), "rev-parse", "HEAD") == head
    cli("finish", "--run-dir", run["run_dir"], "--status", "cancelled")
    assert git(workspace, "for-each-ref", "refs/cr") == ""
