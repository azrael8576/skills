#!/usr/bin/env python3
"""Own one CR invocation: private paths, exact-SHA worktree, compact eval archive.

GitHub I/O goes only through `gh api` (fetch_pr in prepare, _post in publish). No model execution or global PR lock lives here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, astuple, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, TypeVar
from urllib.parse import quote, urlsplit

PR_HOST = "github.com"
SNAPSHOT_FILE = "execution/pr-snapshot.json"


@dataclass(frozen=True)
class RunError:
    error_type: str
    message: str


T = TypeVar("T")


@dataclass(frozen=True)
class Result(Generic[T]):
    value: T | None = None
    error: RunError | None = None


class RunFailure(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error = RunError(error_type, message)


@dataclass(frozen=True)
class Run:
    run_id: str
    mode: str
    project: str
    pr_number: int
    ide: str
    model_id: str
    language: str
    started_at: str
    workspace: str
    run_dir: str
    temp_dir: str
    worktree_path: str
    source_ref: str
    skill_path: str
    remote: str


@dataclass(frozen=True)
class Outcome:
    status: str
    failed_stage: str | None = None
    error: RunError | None = None


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RunFailure("artifact-write-failed", f"Expected absolute path without traversal: {value}")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RunFailure("artifact-write-failed", f"Symlink path is not allowed: {value}")
    return path.resolve()


def _outside(path: Path, workspace: Path) -> None:
    if path == workspace or workspace in path.parents:
        raise RunFailure("artifact-write-failed", f"Run paths must be outside workspace: {path}")


def _write_json(path: Path, value: object) -> None:
    _write(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def _write(path: Path, data: bytes) -> None:
    _path(str(path))
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json(path: Path) -> dict[str, object]:
    _path(str(path))
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunFailure("artifact-write-failed", f"Expected JSON object: {path}")
    return {str(key): item for key, item in value.items()}


def _git(workspace: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", workspace, *args], capture_output=True, text=True, encoding="utf-8", timeout=120)


def initialize(
    *, workspace: str, skill_path: str, project: str, pr_number: int, mode: str,
    ide: str, model_id: str, language: str, temp_root: str, eval_root: str,
    started_at: datetime, run_id: str, remote: str,
) -> Result[Run]:
    """Allocate unique directories. Clock, identity, roots and reviewer are explicit inputs."""
    created: list[Path] = []
    try:
        if mode not in {"normal", "eval"} or pr_number <= 0:
            raise RunFailure("artifact-write-failed", "Expected normal/eval mode and positive PR ID")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", project) or any(part in {".", ".."} for part in project.split("/")):
            raise RunFailure("artifact-write-failed", "Invalid project path")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", remote):
            raise RunFailure("artifact-write-failed", f"Invalid remote name: {remote!r}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", ide) or not model_id.strip() or not language.strip():
            raise RunFailure("artifact-write-failed", "IDE slug, model identifier and language are required")
        if str(uuid.UUID(run_id)) != run_id or started_at.tzinfo is None:
            raise RunFailure("artifact-write-failed", "Expected full UUID and timezone-aware start time")
        workspace_path, skill = _path(workspace), _path(skill_path)
        if not workspace_path.is_dir():
            raise RunFailure("artifact-write-failed", f"Workspace does not exist: {workspace}")
        if _git(str(workspace_path), "rev-parse", "--git-dir").returncode != 0:
            raise RunFailure("worktree-failed", f"Workspace is not a Git repository: {workspace}")
        if not (skill / "references/review-guide.md").is_file():
            raise RunFailure("review-guide-missing", f"Missing {skill}/references/review-guide.md")
        temporary = _path(temp_root) / f"github-cr-{run_id}"
        _outside(temporary, workspace_path)
        timestamp = started_at.astimezone(timezone.utc)
        archive = temporary
        if mode == "eval":
            root = _path(eval_root)
            _outside(root, workspace_path)
            archive = root / PR_HOST / project / f"pr-{pr_number}" / f"{timestamp:%Y%m%dT%H%M%SZ}_{ide}_{run_id}"
            if archive == temporary or temporary in archive.parents or archive in temporary.parents:
                raise RunFailure("artifact-write-failed", "Eval archive and temporary directory must be separate")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.mkdir(mode=0o700)
        created.append(temporary)
        if archive != temporary:
            archive.parent.mkdir(parents=True, exist_ok=True)
            _path(str(archive.parent))
            archive.mkdir(mode=0o700)
            created.append(archive)
        run = Run(run_id, mode, project, pr_number, ide, model_id, language, timestamp.isoformat(),
                  str(workspace_path), str(archive), str(temporary), str(temporary / "worktree"), f"refs/cr/{run_id}/source",
                  # Eval reads the frozen copy so the archived skill is the one actually used.
                  str(archive / "skill") if mode == "eval" else str(skill), remote)
        _write_json(temporary / ".cr-owner.json", asdict(run))
        for name in ("input", "output", "execution"):
            (archive / name).mkdir()
        if mode == "eval":
            # Snapshot only the instructions/code used by this workflow, not caches or eval fixtures.
            for relative in ("SKILL.md", "references", "scripts"):
                source = skill / relative
                destination = archive / "skill" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    for file in source.rglob("*"):
                        if file.is_file() and "__pycache__" not in file.parts:
                            _path(str(file))
                            target = destination / file.relative_to(source)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(file, target)
                else:
                    shutil.copyfile(source, destination)
        skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
        version = re.search(r"^version: (.+)$", skill_text, re.MULTILINE)
        git_version = _git(str(workspace_path), "--version")
        revision = _git(str(skill), "rev-parse", "HEAD")
        dirty = _git(str(skill), "status", "--porcelain", "--", str(skill))
        manifest: dict[str, object] = {
            **asdict(run), "schema_version": 1, "status": "running", "finished_at": None,
            "pr_url": f"https://{PR_HOST}/{project}/pull/{pr_number}",
            "snapshot": None, "input_fingerprint": None, "failed_stage": None, "error": None,
            "publish_status": "skipped_eval" if mode == "eval" else "not_attempted",
            "gates": {"rule": "not_run", "output": "not_run", "structure": "not_run"},
            "versions": {"skill": version.group(1) if version else "unavailable", "gh": "unavailable",
                         "git": git_version.stdout.strip() if git_version.returncode == 0 else "unavailable",
                         "codegraph": "unavailable", "skill_revision": revision.stdout.strip() if revision.returncode == 0 else None,
                         "skill_dirty": bool(dirty.stdout) if dirty.returncode == 0 else None},
            "observability": {key: {"status": "unavailable", "reason": "Host did not export this measurement"}
                              for key in ("host_trace", "tokens", "cost", "model_parameters")},
            "cleanup": {"status": "not_run", "remaining_paths": [str(temporary)]}, "files": [],
        }
        _write_json(archive / "run.json", manifest)
        _record(archive, "stage1-prepare", started_at, started_at, "passed")
        return Result(value=run)
    except (RunFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
        error = exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc))
        for path in reversed(created):
            try:
                shutil.rmtree(path)
            except OSError as cleanup_error:
                error = RunError(error.error_type, f"{error.message}; initialization cleanup failed at {path}: {cleanup_error}")
        return Result(error=error)


def load_run(run_dir: str) -> Result[Run]:
    try:
        root = _path(run_dir)
        manifest = _json(root / "run.json")
        values = {field: manifest.get(field) for field in Run.__dataclass_fields__}
        if any(not isinstance(value, str) for key, value in values.items() if key != "pr_number") or not isinstance(values["pr_number"], int):
            raise RunFailure("artifact-write-failed", "Invalid run identity in manifest")
        run = Run(**values)  # type: ignore[arg-type]
        if run.mode not in {"normal", "eval"} or str(uuid.UUID(run.run_id)) != run.run_id:
            raise RunFailure("artifact-write-failed", "Invalid run identity")
        temporary, workspace = _path(run.temp_dir), _path(run.workspace)
        _outside(root, workspace)
        _outside(temporary, workspace)
        if temporary.name != f"github-cr-{run.run_id}" or run.run_dir != str(root) or run.worktree_path != str(temporary / "worktree") or run.source_ref != f"refs/cr/{run.run_id}/source":
            raise RunFailure("artifact-write-failed", "Run path/ref ownership mismatch")
        if _json(temporary / ".cr-owner.json") != asdict(run):
            raise RunFailure("artifact-write-failed", "Run owner marker mismatch; refusing cleanup")
        _path(run.worktree_path)
        _path(run.skill_path)
        return Result(value=run)
    except (RunFailure, OSError, ValueError) as exc:
        return Result(error=exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc)))


def prepare_worktree(run: Run, source_sha: str, source_remote: str) -> Result[str]:
    """Fetch only the exact missing commit into this invocation's private ref."""
    try:
        owned = load_run(run.run_dir)
        if owned.error:
            raise RunFailure(owned.error.error_type, owned.error.message)
        if owned.value != run:
            raise RunFailure("artifact-write-failed", "Run identity changed")
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha) or not source_remote or source_remote.startswith("-"):
            raise RunFailure("worktree-failed", "Expected full source SHA and a source remote")
        if Path(run.worktree_path).exists():
            raise RunFailure("worktree-failed", "This invocation already has a worktree")
        if _git(run.workspace, "cat-file", "-e", f"{source_sha}^{{commit}}").returncode != 0:
            # A fork PR's head lives in the PR repository only under refs/pull/<n>/head.
            for refspec in (f"{source_sha}:{run.source_ref}", f"+refs/pull/{run.pr_number}/head:{run.source_ref}"):
                fetch = _git(run.workspace, "fetch", "--no-write-fetch-head", "--no-tags", "--no-recurse-submodules", source_remote, refspec)
                if fetch.returncode == 0 and _git(run.workspace, "cat-file", "-e", f"{source_sha}^{{commit}}").returncode == 0:
                    break
            else:
                # Git stderr can contain credentials embedded in remote URLs.
                raise RunFailure("worktree-failed", f"Exact source SHA unavailable: {source_sha}; git fetch exit {fetch.returncode}. Check source remote access and commit availability.")
        added = _git(run.workspace, "worktree", "add", "--detach", run.worktree_path, source_sha)
        if added.returncode != 0:
            raise RunFailure("worktree-failed", added.stderr.strip())
        head = _git(run.worktree_path, "rev-parse", "HEAD")
        if head.returncode != 0 or head.stdout.strip() != source_sha:
            raise RunFailure("worktree-failed", "Detached worktree HEAD does not match PR snapshot")
        return Result(value=source_sha)
    except (RunFailure, OSError, subprocess.SubprocessError) as exc:
        error = exc.error if isinstance(exc, RunFailure) else RunError("worktree-permission-denied" if isinstance(exc, PermissionError) else "worktree-failed", str(exc))
        return Result(error=error)


ARTIFACT_KEYS = ("metadata", "description", "labels", "discussions", "changed_files", "diff", "target_review_rule")
OPTIONAL_ARTIFACTS = {"description", "labels", "discussions"}
SUMMARY_KEYS = {"PR_TITLE": "title", "PR_AUTHOR": "author", "SOURCE_BRANCH": "source", "TARGET_BRANCH": "target", "PR_STATE": "state"}


# Stages run by the agent leave a file; its write time ends that stage.
AGENT_STAGES = (("stage2-rule-gate", "output/security-gates.json"), ("stage3-review", "output/review-draft.md"))


def _stages(root: Path) -> list[dict[str, str]]:
    path = root / "execution/stages.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []


def _record(root: Path, stage: str, started: datetime, finished: datetime, status: str, error: dict[str, str] | None = None) -> None:
    line: dict[str, object] = {"stage": stage, "started_at": started.astimezone(timezone.utc).isoformat(),
                               "finished_at": finished.astimezone(timezone.utc).isoformat(), "status": status}
    if error:
        line["error"] = error
    with (root / "execution/stages.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def _catch_up(root: Path) -> None:
    for stage, relative in AGENT_STAGES:
        path, recorded = root / relative, _stages(root)
        if not path.is_file() or any(line["stage"] == stage for line in recorded):
            continue
        status, error = "passed", None
        if stage == "stage2-rule-gate":
            rule = _json(path).get("rule")
            status = str(rule.get("status")) if isinstance(rule, dict) else "failed"
        elif path.suffix == ".json" and (payload := _json(path)).get("success") is not True:
            status, error = "failed", {"error_type": str(payload.get("error_type")), "message": str(payload.get("message"))}
        finished = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        started = datetime.fromisoformat(recorded[-1]["finished_at"]) if recorded else finished
        _record(root, stage, min(started, finished), finished, status, error)


def _snapshot(run: Run) -> dict[str, object]:
    """The saved PR snapshot, verified complete; its artifacts must sit in this run's input/."""
    response = Path(run.run_dir) / SNAPSHOT_FILE
    if not response.is_file():
        raise RunFailure("artifact-write-failed", f"Missing PR snapshot: {response}")
    payload = _json(response)
    if payload.get("success") is not True:
        raise RunFailure(str(payload.get("error_type") or "artifact-write-failed"), str(payload.get("message") or "Fetching the PR failed"))
    paths, summary = payload.get("artifact_paths"), payload.get("pr_summary")
    if not isinstance(paths, dict) or not isinstance(summary, dict) or not isinstance(payload.get("review_rule_changed_in_pr"), bool):
        raise RunFailure("artifact-write-failed", "PR snapshot lacks artifact_paths, pr_summary or review_rule_changed_in_pr")
    for key in SUMMARY_KEYS.values():
        if not isinstance(summary.get(key), str) or not summary[key].strip():
            raise RunFailure("artifact-write-failed", f"PR snapshot lacks pr_summary.{key}")
    inputs = Path(run.run_dir) / "input"
    for key in ARTIFACT_KEYS:
        value = paths.get(key)
        path = _path(value) if isinstance(value, str) else None
        if path is None or path.parent != inputs or not path.is_file():
            raise RunFailure("artifact-write-failed", f"Artifact {key} is missing or outside {inputs}")
        if key not in OPTIONAL_ARTIFACTS and path.stat().st_size == 0:
            raise RunFailure("artifact-write-failed", f"Artifact {key} is empty: {path}")
    return payload


def _template(skill: Path) -> str:
    text = (skill / "references/review-brief.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^```text\n(.*?)^```$", text.split("## Brief", 1)[-1], re.MULTILINE | re.DOTALL)
    if "## Brief" not in text or len(blocks) != 1:
        raise RunFailure("artifact-write-failed", "review-brief.md must have exactly one text block under ## Brief")
    return str(blocks[0])


def render_brief(run: Run, codegraph_available: bool, now: datetime) -> Result[dict[str, str]]:
    """Write the paths-only review brief the agent reads at Stage 3."""
    try:
        payload = _snapshot(run)
        summary, paths, snapshot = payload["pr_summary"], payload["artifact_paths"], payload.get("snapshot")
        assert isinstance(summary, dict) and isinstance(paths, dict)
        if not isinstance(snapshot, dict):
            raise RunFailure("artifact-write-failed", "PR snapshot has no snapshot SHAs")
        timestamp = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        root = Path(run.run_dir)
        values = {"PR_NUMBER": str(run.pr_number), "PROJECT_PATH": run.project, "MODEL_ID": run.model_id,
                  "REVIEW_TIMESTAMP": timestamp, "USER_LANG": run.language, "WORKTREE_PATH": run.worktree_path,
                  "review_rule_changed_in_pr": str(payload["review_rule_changed_in_pr"]).lower(),
                  "SKILL_PATH": run.skill_path, "CODEGRAPH_AVAILABLE": str(codegraph_available).lower(),
                  "DRAFT_PATH": str(root / "output/review-draft.md"),
                  "BASE_SHA": str(snapshot.get("merge_base_sha")), "SOURCE_SHA": str(snapshot.get("source_sha")),
                  **{name: str(summary[key]) for name, key in SUMMARY_KEYS.items()},
                  **{f"artifact_paths.{key}": str(paths[key]) for key in ARTIFACT_KEYS}}

        def substitute(match: re.Match[str]) -> str:
            if match.group(1) not in values:
                raise RunFailure("artifact-write-failed", f"Unknown prompt placeholder: {match.group(0)}")
            return values[match.group(1)]

        # One pass, so braces inside values (e.g. an PR title) are never expanded.
        text = re.sub(r"\{([A-Za-z_][\w.]*)\}", substitute, _template(Path(run.skill_path)))
        manifest = _json(root / "run.json")
        manifest["bindings"] = {"review_timestamp": timestamp, "codegraph_available": codegraph_available}
        path = root / "execution/review-brief.md"
        _write(path, text.encode("utf-8"))
        _write_json(root / "run.json", manifest)
        return Result(value={"brief_path": str(path), "review_timestamp": timestamp})
    except (RunFailure, OSError, ValueError) as exc:
        return Result(error=exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc)))


# Review Output Gate (rules mirrored in README § Gates). Favor false positives over public leakage.
LEAK_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("certificate", re.compile(r"-----BEGIN CERTIFICATE-----")),
    ("ssh-key", re.compile(r"\b(?:ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp\d+) AAAA[0-9A-Za-z+/]{20,}")),
    ("token", re.compile(r"\bglpat-[\w-]{20,}|\bgh[po]_[A-Za-z0-9]{30,}|\bsk-[\w-]{20,}|\bAKIA[0-9A-Z]{16}\b")),
    ("password", re.compile(r"(?i)(?:password|passwd|密碼)\s*[:：=]\s*[\"'](?!\*+[\"'])[^\"'\s]{4,}[\"']")),
    ("system-file", re.compile(r"^[a-z_][\w.-]*:[x*!]?:\d+:\d+:|^[\w.-]+:\$(?:1|2[aby]|5|6|y)\$")),
)
BASE64 = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")
ENV_LINE = re.compile(r"^\s*[A-Z][A-Z0-9_]*=\S")
FINDING_ROW = re.compile(r"^\|\s*(?:🔴|🟡|🟢)", re.MULTILINE)


def output_gate_failures(review: str, changed_files: list[str], diff: str) -> list[str]:
    """Evidence names line and category only, never the matched text."""
    failures: list[str] = []
    env_run = 0
    for number, line in enumerate(review.splitlines(), 1):
        failures += [f"line {number}: {name}" for name, pattern in LEAK_PATTERNS if pattern.search(line)]
        if any(token not in diff and re.search("[A-Z]", token) and re.search("[a-z]", token) and re.search("[0-9]", token)
               for token in BASE64.findall(line)):
            failures.append(f"line {number}: base64")
        env_run = env_run + 1 if ENV_LINE.match(line) else 0
        if env_run == 5:
            failures.append(f"line {number}: env-dump")
    cited = any(path in review or Path(path).name in review for path in changed_files)
    if FINDING_ROW.search(review) and not cited:
        failures.append("relevance: no changed file is cited")
    return failures


def validate(run: Run, clock: Callable[[], datetime]) -> Result[dict[str, object]]:
    """Stage 4 validation: structure validator + output gate; only a passing draft becomes output/review.md."""
    root = Path(run.run_dir)
    draft, review, gates_path = root / "output/review-draft.md", root / "output/review.md", root / "output/security-gates.json"
    _catch_up(root)
    started = clock()
    try:
        if not draft.is_file():
            raise RunFailure("review-validation-failed", f"The review phase wrote no draft: {draft}")
        gates = _json(gates_path) if gates_path.is_file() else {}
        rule = gates.get("rule")
        if not isinstance(rule, dict) or rule.get("status") != "passed":
            raise RunFailure("review-validation-failed", "Target review rule gate has not passed")
        payload = _snapshot(run)
        bindings = _json(root / "run.json").get("bindings")
        summary, paths = payload["pr_summary"], payload["artifact_paths"]
        assert isinstance(summary, dict) and isinstance(paths, dict)
        if not isinstance(bindings, dict):
            raise RunFailure("review-validation-failed", "Run has no prompt bindings; render the prompt first")
        validator = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("validate_review_structure.py")), "--review-file", str(draft),
             "--pr-number", str(run.pr_number), "--source", str(summary["source"]), "--target", str(summary["target"]),
             "--author", str(summary["author"]), "--model-id", run.model_id, "--timestamp", str(bindings["review_timestamp"]),
             *(["--require-codegraph-marker"] if bindings.get("codegraph_available") is True else [])],
            capture_output=True, timeout=120)
        _write(root / "output/validation.json", validator.stdout)
        structure = json.loads(validator.stdout)
        text = draft.read_text(encoding="utf-8")
        changed = Path(str(paths["changed_files"])).read_text(encoding="utf-8").split()
        leaks = output_gate_failures(text, changed, Path(str(paths["diff"])).read_text(encoding="utf-8"))
        leaks += [f"isolation: {problem}" for problem in isolation_problems(run)]
        gates["output"] = {"status": "failed" if leaks else "passed", "evidence": leaks or ["No secret-leak pattern; a changed file is cited or there are no findings"]}
        _write_json(gates_path, gates)
        failures = [str(item["message"]) for item in structure.get("failures", [])] + leaks
        if failures or structure.get("ok") is not True:
            raise RunFailure("review-validation-failed", "; ".join(failures))
        _write(review, draft.read_bytes())
        verdict = next((line.strip() for line in text.splitlines() if line.startswith("**Verdict")), None)
        _record(root, "stage4-validate", started, clock(), "passed")
        return Result(value={"ok": True, "verdict": verdict, "review_path": str(review)})
    except (RunFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
        review.unlink(missing_ok=True)
        error = exc.error if isinstance(exc, RunFailure) else RunError("review-validation-failed", str(exc))
        _record(root, "stage4-validate", started, clock(), "failed", asdict(error))
        return Result(error=error)


def _origin_path(url: str) -> str:
    """Project path of https://, ssh:// or scp-style (git@host:path) remotes; never the credentials."""
    path = urlsplit(url).path if "://" in url else url.split(":", 1)[-1]
    return path.strip("/").removesuffix(".git")


def parse_pr_url(url: str) -> Result[tuple[str, int]]:
    """Stage 1: only github.com pull request URLs are in scope."""
    match = re.fullmatch(rf"https?://{re.escape(PR_HOST)}/([\w.\-]+/[\w.\-]+)/pull/([1-9]\d*)(?:[/?#]\S*)?", url.strip())
    if not match:
        return Result(error=RunError("invalid-pr-url", f"Not a {PR_HOST} pull request URL: {url}"))
    return Result(value=(match.group(1), int(match.group(2))))


def matching_remote(workspace: str, project: str) -> Result[str]:
    """Stage 1: origin, or upstream in a fork clone, must be the PR repository before anything is allocated."""
    seen: list[str] = []
    for name in ("origin", "upstream"):
        url = _git(workspace, "remote", "get-url", name) if Path(workspace).is_dir() else None
        if url is None or url.returncode != 0:
            continue
        current = _origin_path(url.stdout.strip())
        if current.lower() == project.lower():  # GitHub owner and repository names are case-insensitive
            return Result(value=name)
        seen.append(f"{name} is {current!r}")
    found = "; ".join(seen) or "Workspace has no origin or upstream remote"
    return Result(error=RunError("workspace-mismatch", f"{found}; PR repository is {project!r}; open a local clone of it and retry"))


GH_MIN_VERSION = (2, 48)  # `gh api --paginate --slurp`


def gh_version(gh: list[str]) -> Result[str]:
    """Stage 1: gh must run and support `--slurp`; authentication is checked by the first API call."""
    try:
        done = subprocess.run([*gh, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result(error=RunError("gh-unavailable", f"GitHub CLI could not run: {exc}; install gh 2.48 or later"))
    found = re.search(r"gh version (\d+)\.(\d+)\S*", done.stdout)
    if done.returncode != 0 or not found or (int(found.group(1)), int(found.group(2))) < GH_MIN_VERSION:
        return Result(error=RunError("gh-unavailable", f"Found {found.group(0) if found else 'no gh version'}; install gh 2.48 or later"))
    return Result(value=found.group(0))


def _gh(gh: list[str], args: list[str], error_type: str) -> str:
    """One `gh api` call on github.com; a failure names the HTTP status, never gh's raw stderr."""
    try:
        done = subprocess.run([*gh, "api", "--hostname", PR_HOST, *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunFailure("gh-unavailable", f"GitHub CLI could not run: {exc}") from exc
    if done.returncode == 0:
        return done.stdout
    status = re.search(r"\(HTTP (\d{3})\)", done.stderr)
    if done.returncode == 4 or (status and status.group(1) == "401"):  # gh exits 4 when no account is logged in
        raise RunFailure("auth-failed", f"GitHub CLI is not authenticated for {PR_HOST}; run `gh auth login`")
    raise RunFailure(error_type, f"GitHub returned HTTP {status.group(1)}" if status else f"gh api exited {done.returncode}")


def _gh_json(gh: list[str], args: list[str], error_type: str) -> object:
    try:
        value: object = json.loads(_gh(gh, args, error_type))
    except json.JSONDecodeError as exc:
        raise RunFailure(error_type, f"gh api returned no JSON for {args[0]}") from exc
    return value


def _gh_object(gh: list[str], endpoint: str, error_type: str) -> dict[str, object]:
    value = _gh_json(gh, [endpoint], error_type)
    if not isinstance(value, dict):
        raise RunFailure(error_type, f"GitHub returned an unexpected payload for {endpoint}")
    return value


def _gh_pages(gh: list[str], endpoint: str, error_type: str) -> list[dict[str, object]]:
    pages = _gh_json(gh, ["--paginate", "--slurp", endpoint], error_type)
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise RunFailure(error_type, f"GitHub returned an unexpected list payload for {endpoint}")
    items = [item for page in pages for item in page]
    if not all(isinstance(item, dict) for item in items):
        raise RunFailure(error_type, f"GitHub returned an unexpected list item for {endpoint}")
    return items


def _sha(value: object, error_type: str, what: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value.strip()):
        raise RunFailure(error_type, f"{what} is missing or not a full commit SHA")
    return value.strip()


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


AI_HEADER = "### 🤖 AI Code Review"


def _human_discussions(kind: str, items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Existing human discussion; bots and earlier AI reviews (posted under a human account by gh) are dropped."""
    return [{"kind": kind, "id": item.get("id"), "author": _dict(item.get("user")).get("login"),
             "body": item.get("body") or "", "created_at": item.get("created_at") or item.get("submitted_at"),
             "updated_at": item.get("updated_at"), "state": item.get("state"), "path": item.get("path"),
             "line": item.get("line"), "in_reply_to_id": item.get("in_reply_to_id")}
            for item in items
            if _dict(item.get("user")).get("type") != "Bot" and AI_HEADER not in str(item.get("body") or "")]


def _unified_diff(change: dict[str, object]) -> str:
    new = str(change.get("filename") or "")
    old = str(change.get("previous_filename") or new)
    old_header = "/dev/null" if change.get("status") == "added" else f"a/{old}"
    new_header = "/dev/null" if change.get("status") == "removed" else f"b/{new}"
    return f"diff --git a/{old} b/{new}\n--- {old_header}\n+++ {new_header}\n{change.get('patch') or ''}".rstrip()


def _summary(pr: dict[str, object]) -> dict[str, object]:
    head, base = _dict(pr.get("head")), _dict(pr.get("base"))
    return {"title": pr.get("title"), "source": head.get("ref"), "target": base.get("ref"),
            "author": _dict(pr.get("user")).get("login"),
            "state": "merged" if pr.get("merged") is True else pr.get("state")}


def _review_rule(gh: list[str], project: str, branch: str, rule_path: str) -> tuple[str, str, object]:
    """Target-branch rule pinned to the branch's current commit, so the snapshot names the exact version read."""
    commit = _sha(_gh(gh, [f"repos/{project}/branches/{quote(branch, safe='')}", "--jq", ".commit.sha"], "permission-denied"),
                  "permission-denied", f"Target branch {branch} head")
    try:
        payload = _gh_object(gh, f"repos/{project}/contents/{quote(rule_path)}?ref={commit}", "permission-denied")
    except RunFailure as exc:
        if "HTTP 404" in exc.error.message:
            raise RunFailure("no-review-rule", f"{rule_path} does not exist on target branch {branch}") from exc
        raise
    raw = payload.get("content")
    try:
        content = base64.b64decode(raw).decode("utf-8") if payload.get("encoding") == "base64" and isinstance(raw, str) else ""
    except (ValueError, UnicodeDecodeError) as exc:
        raise RunFailure("no-review-rule", f"{rule_path} on target branch {branch} is not valid UTF-8 text") from exc
    if not content.strip():
        raise RunFailure("no-review-rule", f"{rule_path} is missing, empty or unreadable on target branch {branch}")
    return content, commit, payload.get("sha")


def fetch_pr(run: Run, gh: list[str], clock: Callable[[], datetime], rule_path: str = "review-rule.md") -> Result[dict[str, object]]:
    """Stage 1 fetch: write the seven frozen inputs and the PR snapshot, also on failure."""
    root = Path(run.run_dir)
    response = root / SNAPSHOT_FILE
    started = clock()
    repo, number = f"repos/{run.project}", run.pr_number
    paths: dict[str, str] = {}

    def write(key: str, name: str, text: str) -> None:
        # Each group is saved before the next network call, so a failed eval keeps what it fetched.
        path = root / "input" / f"pr-{number}-{name}"
        _write(path, text.encode("utf-8"))
        paths[key] = str(path)

    if response.exists():
        return Result(error=RunError("artifact-write-failed", "This invocation already fetched its inputs"))
    try:
        version = gh_version(gh)
        if version.error or version.value is None:
            raise RunFailure(*(astuple(version.error) if version.error else ("gh-unavailable", "No gh version")))
        manifest = _json(root / "run.json")
        versions = manifest.get("versions")
        if isinstance(versions, dict):
            versions["gh"] = version.value
            _write_json(root / "run.json", manifest)
        pr = _gh_object(gh, f"{repo}/pulls/{number}", "permission-denied")
        head, base = _dict(pr.get("head")), _dict(pr.get("base"))
        source_sha = _sha(head.get("sha"), "diff-fetch-failed", "PR head SHA")
        target_sha = _sha(base.get("sha"), "diff-fetch-failed", "PR base SHA")
        summary = _summary(pr)
        warnings = [{"type": "pr-state", "message": f"PR state is {summary['state']}"}] if summary["state"] != "open" else []
        warnings += [{"type": "draft", "message": "PR is marked as draft"}] if pr.get("draft") is True else []
        metadata = {key: pr.get(key) for key in ("number", "title", "state", "draft", "merged", "html_url", "created_at", "updated_at", "changed_files")}
        metadata.update(author=summary["author"], source_branch=summary["source"], target_branch=summary["target"],
                        head_sha=source_sha, base_sha=target_sha, warnings=warnings,
                        source_repo=_dict(head.get("repo")).get("full_name"), target_repo=_dict(base.get("repo")).get("full_name"))
        raw_labels = pr.get("labels")
        labels = [str(_dict(label).get("name")) for label in (raw_labels if isinstance(raw_labels, list) else [])
                  if _dict(label).get("name")]
        write("metadata", "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
        write("description", "description.md", str(pr.get("body") or ""))
        write("labels", "labels.txt", "\n".join(labels))
        files = _gh_pages(gh, f"{repo}/pulls/{number}/files?per_page=100", "diff-fetch-failed")
        if not files:
            raise RunFailure("diff-fetch-failed", "PR changes are empty")
        if pr.get("changed_files") != len(files):
            raise RunFailure("diff-fetch-failed", f"GitHub listed {len(files)} of {pr.get('changed_files')} changed files (the files API stops at 3000)")
        # GitHub drops `patch` for oversized text diffs; binary files have none and no line counts.
        missing = [str(change.get("filename")) for change in files
                   if not change.get("patch") and (int(str(change.get("additions") or 0)) + int(str(change.get("deletions") or 0))) > 0]
        if missing:
            raise RunFailure("diff-fetch-failed", f"GitHub returned no diff text for {len(missing)} file(s): {', '.join(missing)}")
        merge_base = _sha(_gh(gh, [f"{repo}/compare/{target_sha}...{source_sha}", "--jq", ".merge_base_commit.sha"], "diff-fetch-failed"),
                          "diff-fetch-failed", "Merge base")
        again = _gh_object(gh, f"{repo}/pulls/{number}", "diff-fetch-failed")
        if (_dict(again.get("head")).get("sha"), _dict(again.get("base")).get("sha")) != (source_sha, target_sha):
            raise RunFailure("diff-fetch-failed", "PR snapshot changed between metadata and diff; retry a new run")
        write("diff", "diff.txt", "\n".join(_unified_diff(change) for change in files))
        write("changed_files", "changed-files.txt", "\n".join(str(change.get("filename")) for change in files))
        discussions = (_human_discussions("review_comment", _gh_pages(gh, f"{repo}/pulls/{number}/comments?per_page=100", "discussions-fetch-failed"))
                       + _human_discussions("review", _gh_pages(gh, f"{repo}/pulls/{number}/reviews?per_page=100", "discussions-fetch-failed"))
                       + _human_discussions("issue_comment", _gh_pages(gh, f"{repo}/issues/{number}/comments?per_page=100", "discussions-fetch-failed")))
        write("discussions", "discussions.txt", json.dumps(discussions, ensure_ascii=False, indent=2, sort_keys=True))
        rule, rule_commit, rule_blob = _review_rule(gh, run.project, str(summary["target"]), rule_path)
        write("target_review_rule", "target-review-rule.md", rule)
        payload: dict[str, object] = {
            "success": True, "repo": run.project, "pr_number": number, "artifact_paths": paths, "warnings": warnings,
            "review_rule_changed_in_pr": any(rule_path in (change.get("filename"), change.get("previous_filename")) for change in files),
            "snapshot": {"source_sha": source_sha, "diff_target_sha": target_sha, "merge_base_sha": merge_base,
                         "review_rule_commit_sha": rule_commit, "review_rule_blob_sha": rule_blob,
                         "source_project_id": _dict(head.get("repo")).get("id"), "target_project_id": _dict(base.get("repo")).get("id")},
            "pr_summary": summary,
        }
    except (RunFailure, OSError, ValueError) as exc:
        error = exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc))
        payload = {"success": False, **asdict(error)}
    try:
        _write_json(response, payload)
    except (RunFailure, OSError) as exc:
        return Result(error=exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc)))
    failed = None if payload["success"] is True else {"error_type": str(payload["error_type"]), "message": str(payload["message"])}
    _record(root, "stage1-fetch", started, clock(), "failed" if failed else "passed", failed)
    if failed:
        return Result(error=RunError(failed["error_type"], failed["message"]))
    return Result(value={"snapshot_path": str(response)})


def _post(run: Run, gh: list[str]) -> Result[dict[str, object]]:
    """One PR comment with output/review.md byte for byte; only `publish` calls it, right after validation."""
    review = Path(run.run_dir) / "output/review.md"
    try:
        comment = _dict(_gh_json(gh, [f"repos/{run.project}/issues/{run.pr_number}/comments", "--method", "POST",
                                      "-F", f"body=@{review}"], "post-failed"))
        if not comment.get("id") or not isinstance(comment.get("html_url"), str):
            raise RunFailure("post-failed", "GitHub comment creation returned an unexpected payload")
        return Result(value={"comment_id": comment["id"], "html_url": comment["html_url"]})
    except RunFailure as exc:
        return Result(error=exc.error)


def publish(run: Run, gh: list[str], clock: Callable[[], datetime]) -> Result[dict[str, object]]:
    """Stage 4: validate the draft, then post it (normal) or keep it as the intended comment (eval)."""
    validated = validate(run, clock)
    if validated.error or validated.value is None:
        return validated
    root = Path(run.run_dir)
    started = clock()
    value: dict[str, object] = dict(validated.value)
    status, error = "skipped_eval", None
    if run.mode == "normal":
        for _ in range(2):  # one retry for a transient failure
            posted = _post(run, gh)
            if posted.error is None:
                break
        status, error = ("failed", posted.error) if posted.error else ("posted", None)
        value.update(posted.value or {})
    try:
        manifest = _json(root / "run.json")
        manifest["publish_status"] = status
        manifest["comment_url"] = value.get("html_url")
        _write_json(root / "run.json", manifest)
    except (RunFailure, OSError) as exc:
        error = exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc))
    _record(root, "stage4-publish", started, clock(), status, asdict(error) if error else None)
    if error:
        return Result(error=error)
    return Result(value={**value, "publish_status": status})


def _codegraph(run: Run, command: list[str], timeout: float) -> bool:
    """codegraph-optional: any failure only means the reviewer works without an index."""
    log = Path(run.run_dir) / "execution/codegraph.log"
    with log.open("wb") as handle:
        try:
            process = subprocess.Popen([shutil.which(command[0]) or command[0], *command[1:]], cwd=run.worktree_path,
                                       stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            handle.write(f"codegraph could not start: {exc}\n".encode())
            return False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # npx spawns node; kill the whole group so nothing keeps writing to the log.
            os.killpg(process.pid, signal.SIGKILL) if hasattr(os, "killpg") else process.kill()
            process.wait()
            handle.write(f"\ncodegraph timed out after {timeout:g}s\n".encode())
            return False
    return code == 0 and (Path(run.worktree_path) / ".codegraph").is_dir()


def _base_ref(run: Run) -> str:
    return run.source_ref.rsplit("/", 1)[0] + "/base"


def _touched_paths(changed_files: Path, diff: Path) -> set[str]:
    """GitHub's changed files plus the old side of each change, taken from the `diff --git` header's `--- ` line."""
    paths = {line.strip() for line in changed_files.read_text(encoding="utf-8").splitlines() if line.strip()}
    lines = diff.read_text(encoding="utf-8").splitlines()
    for header, old in zip(lines, lines[1:]):
        if header.startswith("diff --git ") and old.startswith("--- a/"):
            paths.add(old[len("--- a/"):])
    return paths


def _check_change_list(run: Run, base_sha: str, source_sha: str, source_remote: str, changed_files: Path,
                       diff: Path) -> None:
    """The review reads the change with `git diff BASE SOURCE`; it must touch exactly GitHub's paths.

    Renames are compared on both sides (`--no-renames` locally): git and GitHub pair renames with different
    similarity thresholds, so a heavily edited rename lists one path on one side and two on the other.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise RunFailure("worktree-failed", "PR snapshot has no full merge base SHA")
    if _git(run.workspace, "cat-file", "-e", f"{base_sha}^{{commit}}").returncode != 0:
        fetch = _git(run.workspace, "fetch", "--no-write-fetch-head", "--no-tags", "--no-recurse-submodules",
                     source_remote, f"{base_sha}:{_base_ref(run)}")
        if fetch.returncode != 0:
            raise RunFailure("worktree-failed", f"Merge base unavailable: {base_sha}; git fetch exit {fetch.returncode}.")
    listed = _git(run.worktree_path, "diff", "--name-only", "--no-renames", "-z", base_sha, source_sha)
    if listed.returncode != 0:
        raise RunFailure("worktree-failed", f"git diff --name-only failed: {listed.stderr.strip()}")
    local = {name for name in listed.stdout.split("\0") if name}
    gitlab = _touched_paths(changed_files, diff)
    if local != gitlab:
        only_local, only_gitlab = sorted(local - gitlab)[:5], sorted(gitlab - local)[:5]
        raise RunFailure("worktree-failed", f"Local git lists {len(local)} changed files, GitHub {len(gitlab)}; "
                         f"only local: {only_local}; only GitHub: {only_gitlab}")


def prepare_local(run: Run, source_remote: str | None, codegraph_command: list[str], codegraph_timeout: float,
                  clock: Callable[[], datetime]) -> Result[dict[str, object]]:
    """Stage 1 local part: exact-SHA worktree, merge base, best-effort index, then the review brief."""
    root = Path(run.run_dir)
    source_remote = source_remote or run.remote
    _catch_up(root)
    started = clock()
    try:
        payload = _snapshot(run)
        snapshot, paths = payload.get("snapshot"), payload["artifact_paths"]
        assert isinstance(paths, dict)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("source_sha"), str):
            raise RunFailure("diff-fetch-failed", "PR snapshot has no snapshot.source_sha")
        worktree = prepare_worktree(run, str(snapshot["source_sha"]), source_remote)
        if worktree.error:
            raise RunFailure(worktree.error.error_type, worktree.error.message)
        _check_change_list(run, str(snapshot.get("merge_base_sha")), str(snapshot["source_sha"]), source_remote,
                           Path(str(paths["changed_files"])), Path(str(paths["diff"])))
        available = _codegraph(run, codegraph_command, codegraph_timeout)
        rendered = render_brief(run, available, clock())
        if rendered.error or rendered.value is None:
            raise RunFailure(*(astuple(rendered.error) if rendered.error else ("artifact-write-failed", "No brief")))
        rule = Path(str(paths["target_review_rule"]))
        result: dict[str, object] = {"worktree_path": run.worktree_path, "codegraph_available": available, **rendered.value,
                                     "review_rule_path": str(rule), "review_rule": rule.read_text(encoding="utf-8")}
        if not available:
            lines = (root / "execution/codegraph.log").read_text(encoding="utf-8", errors="replace").splitlines()
            result["codegraph_log_tail"] = "\n".join(lines[-20:])
        _record(root, "stage1-worktree", started, clock(), "passed")
        return Result(value=result)
    except (RunFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
        error = exc.error if isinstance(exc, RunFailure) else RunError("worktree-failed", str(exc))
        _record(root, "stage1-worktree", started, clock(), "failed", asdict(error))
        return Result(error=error)


# The review phase writes only the draft; gates are written by Stage 2 and publish (which rewrites them on a rerun).
REVIEWER_OUTPUTS = {"review-draft.md", "security-gates.json", "validation.json", "review.md"}


def isolation_problems(run: Run) -> list[str]:
    """The review phase may write only the draft; the worktree may differ only by Codegraph's index."""
    if not Path(run.worktree_path).is_dir():
        return [f"worktree is missing: {run.worktree_path}"]
    status = _git(run.worktree_path, "status", "--porcelain", "--untracked-files=all", "-z")
    if status.returncode != 0:
        return [f"worktree status failed: {status.stderr.strip()}"]
    problems = [f"worktree changed: {entry[3:]}" for entry in status.stdout.split("\0")
                if entry and not entry[3:].startswith(".codegraph/")]
    output = Path(run.run_dir) / "output"
    problems += [f"unexpected output file: {path.name}" for path in sorted(output.iterdir()) if path.name not in REVIEWER_OUTPUTS]
    return problems


def _gates(root: Path) -> dict[str, str]:
    result = {"rule": "not_run", "output": "not_run", "structure": "not_run"}
    security = root / "output/security-gates.json"
    if security.exists():
        for name, value in _json(security).items():
            if name in {"rule", "output"} and isinstance(value, dict) and value.get("status") in {"passed", "failed", "not_run"}:
                result[name] = str(value["status"])
            else:
                raise RunFailure("review-validation-failed", f"Invalid security gate record: {name}")
    validation = root / "output/validation.json"
    if validation.exists():
        payload = _json(validation)
        if not isinstance(payload.get("ok"), bool):
            raise RunFailure("review-validation-failed", "Invalid validator result")
        result["structure"] = "passed" if payload["ok"] else "failed"
    return result


def _files(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        _path(str(path))
        if path.is_file() and path not in {root / "run.json", root / "README.md", root / ".cr-owner.json"}:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            records.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "purpose": relative.split("/")[0]})
    return records


def _readme(manifest: dict[str, object], files: list[dict[str, object]]) -> bytes:
    snapshot = manifest.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    lines = [f"# CR Eval · #{manifest['pr_number']}", "", f"[{manifest['project']}]({manifest['pr_url']})", "",
             "| 執行 | 紀錄 |", "| --- | --- |",
             *[f"| {label} | `{manifest.get(key)}` |" for label, key in (("狀態", "status"), ("IDE", "ide"), ("模型", "model_id"), ("開始 UTC", "started_at"), ("完成 UTC", "finished_at"), ("張貼", "publish_status"))], "",
             "## 固定輸入", "", "| 節點 | SHA |", "| --- | --- |",
             *[f"| {label} | `{snapshot.get(key, 'unavailable')}` |" for label, key in (("PR head", "source_sha"), ("Diff target", "diff_target_sha"), ("Merge base", "merge_base_sha"), ("Review rule 實際版本", "review_rule_commit_sha"))], "",
             f"輸入指紋：`{manifest.get('input_fingerprint') or 'unavailable（輸入未完整取得）'}`", "",
             "## 驗收", "", "| Gate | 結果 |", "| --- | --- |"]
    gates = manifest["gates"]
    if isinstance(gates, dict):
        lines += [f"| {name} | {status} |" for name, status in gates.items()]
    lines += ["",
             f"錯誤：`{json.dumps(manifest.get('error'), ensure_ascii=False)}` · 階段：`{manifest.get('failed_stage')}`", "",
             f"清理：`{json.dumps(manifest['cleanup'], ensure_ascii=False)}`", "",
             "## 閱讀結果", "", "[機器可讀紀錄與檔案雜湊](run.json)", ""]
    available = {str(file["path"]) for file in files}
    for name, label in (("output/review.md", "預定張貼留言"), ("output/review-draft.md", "原始草稿"),
                        ("output/validation.json", "格式驗證"), ("output/security-gates.json", "安全 gate 證據"),
                        ("execution/review-brief.md", "實際 review brief")):
        if name in available:
            lines.append(f"- [{label}]({name})")
    total = sum(int(str(file["bytes"])) for file in files)
    lines += ["", "## 產物", "", f"{len(files)} 個檔案 · {total:,} bytes（不含此索引與 run.json）", ""]
    for record in files:
        relative = str(record["path"])
        lines.append(f"- [{relative}]({quote(relative, safe='/')}) · {record['bytes']} bytes")
    lines += ["", "## 重現與評分", "", "以相同 input_fingerprint 配對不同 IDE／模型的 run；評分使用 output/review.md，gate 失敗則僅有草稿。",
              "Git 程式碼未封存。先從本機 Git 或原專案 remote 取得表中的完整 SHA，再建立 detached worktree；取不到即回報重現失敗，不能改用最新 branch。",
              "重跑時沿用本目錄 input/ 的 PR 描述、討論、diff 與規則，避免重新抓取已變動的內容。", "",
              "## 可觀測資料", "", f"`{json.dumps(manifest['observability'], ensure_ascii=False)}`", "",
              "僅保存 host 實際可匯出的 trace／用量；未取得的數據標為 unavailable。不要保存 credentials 或環境變數。", ""]
    return "\n".join(lines).encode("utf-8")


def _cleanup(run: Run) -> dict[str, object]:
    temporary = Path(run.temp_dir)
    try:
        errors: list[str] = []
        worktree = Path(run.worktree_path)
        if worktree.exists():
            removed = _git(run.workspace, "worktree", "remove", "--force", str(worktree))
            # A directory can exist before Git registered it; remove it only when unregistered.
            listed = _git(run.workspace, "worktree", "list", "--porcelain", "-z")
            registered = any(
                record.startswith("worktree ") and Path(record[9:]).resolve() == worktree.resolve()
                for record in listed.stdout.split("\0")
            )
            if removed.returncode != 0 and (listed.returncode != 0 or registered):
                errors.append(f"worktree cleanup failed: {removed.stderr.strip()}")
            elif worktree.exists():
                shutil.rmtree(worktree)
        for name in (run.source_ref, _base_ref(run)):
            ref = _git(run.workspace, "show-ref", "--verify", "--quiet", name)
            if ref.returncode == 0:
                deleted = _git(run.workspace, "update-ref", "-d", name)
                if deleted.returncode != 0:
                    errors.append(f"ref cleanup failed: {deleted.stderr.strip()}")
            elif ref.returncode not in {1, 128}:
                errors.append(f"ref lookup failed: {ref.stderr.strip()}")
        if not errors:
            try:
                shutil.rmtree(temporary)
            except OSError as exc:
                errors.append(str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    return {"status": "failed" if errors else "completed", "remaining_paths": [str(temporary)] if errors else [], "errors": errors}


def finish(run: Run, outcome: Outcome, finished_at: datetime) -> Result[dict[str, object]]:
    """Persist/checksum eval assets before owner-only cleanup; cleanup is a separate outcome."""
    root, temporary = Path(run.run_dir), Path(run.temp_dir)
    ownership_verified = False
    try:
        owned = load_run(run.run_dir)
        if owned.error:
            raise RunFailure(owned.error.error_type, owned.error.message)
        if owned.value != run:
            raise RunFailure("artifact-write-failed", "Run identity changed")
        if outcome.status not in {"completed", "failed", "cancelled"} or finished_at.tzinfo is None:
            raise RunFailure("review-validation-failed", "Invalid terminal status or finish time")
        ownership_verified = True
        _catch_up(root)
        manifest = _json(root / "run.json")
        publish_status = manifest.get("publish_status")  # written by initialize and publish, never by the agent
        gates = _gates(root)
        draft, review = root / "output/review-draft.md", root / "output/review.md"
        if outcome.status == "completed" and (any(value != "passed" for value in gates.values()) or not draft.is_file() or (run.mode == "normal" and publish_status != "posted")):
            raise RunFailure("review-validation-failed", "Completed runs require a draft, all gates passed and the mode's publish outcome")
        if all(value == "passed" for value in gates.values()) and draft.is_file():
            _write(review, draft.read_bytes())
        else:
            review.unlink(missing_ok=True)
        response = root / SNAPSHOT_FILE
        snapshot: object = None
        fingerprint: str | None = None
        if response.exists():
            payload = _json(response)
            if payload.get("success") is True:
                snapshot = payload.get("snapshot")
                if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(key), str) or not re.fullmatch(r"[0-9a-f]{40}", str(snapshot[key])) for key in ("source_sha", "diff_target_sha", "merge_base_sha", "review_rule_commit_sha")):
                    raise RunFailure("diff-fetch-failed", "Invalid PR snapshot")
                inputs = _files(root / "input")
                if len(inputs) != 7:
                    raise RunFailure("artifact-write-failed", "Expected seven frozen input files for fingerprint")
                # Run identity, paths, model and time are deliberately absent.
                context = {key: snapshot[key] for key in ("source_sha", "diff_target_sha", "merge_base_sha", "review_rule_commit_sha")}
                fingerprint = hashlib.sha256(json.dumps({"snapshot": context, "inputs": [{"path": file["path"], "sha256": file["sha256"]} for file in inputs]}, sort_keys=True).encode()).hexdigest()
        if outcome.status == "completed" and fingerprint is None:
            raise RunFailure("artifact-write-failed", "Completed runs require a PR snapshot and all seven frozen input files")
        manifest.update(status=outcome.status, finished_at=finished_at.astimezone(timezone.utc).isoformat(),
                        gates=gates, failed_stage=outcome.failed_stage,
                        error=asdict(outcome.error) if outcome.error else None, snapshot=snapshot, input_fingerprint=fingerprint)
        if run.mode == "eval":
            _write_json(root / "run.json", manifest)
            index = Path(run.worktree_path) / ".codegraph"
            if index.exists():
                _path(str(index))
                for path in index.rglob("*"):
                    _path(str(path))
                shutil.copytree(index, root / "assets/codegraph", dirs_exist_ok=True)
            observability = manifest.get("observability")
            runtime_path = root / "execution/runtime.json"
            if runtime_path.exists():
                runtime = _json(runtime_path)
                versions = manifest.get("versions")
                extra_versions = runtime.get("versions", {})
                if not isinstance(extra_versions, dict) or not isinstance(versions, dict):
                    raise RunFailure("artifact-write-failed", "Invalid runtime versions")
                for name, version in extra_versions.items():
                    if name not in {"gh", "codegraph"} or not isinstance(version, str) or not version.strip():
                        raise RunFailure("artifact-write-failed", f"Invalid runtime version: {name}")
                    versions[name] = version
                measurements = runtime.get("observability", {})
                if not isinstance(measurements, dict) or not isinstance(observability, dict):
                    raise RunFailure("artifact-write-failed", "Invalid runtime measurements")
                for name, record in measurements.items():
                    if name not in {"tokens", "cost", "model_parameters"} or not isinstance(record, dict) or record.get("status") != "available" or "value" not in record or not isinstance(record.get("source"), str):
                        raise RunFailure("artifact-write-failed", f"Invalid runtime measurement: {name}")
                    observability[name] = record
            if isinstance(observability, dict) and (root / "execution/host-trace.jsonl").is_file():
                observability["host_trace"] = {"status": "available", "path": "execution/host-trace.jsonl"}
            files = _files(root)
            manifest["files"] = files
            # Do not remove the only copy of an index when persistence fails.
            _write_json(root / "run.json", manifest)
            _write(root / "README.md", _readme(manifest, files))
        manifest["cleanup"] = _cleanup(run)
        if run.mode == "eval":
            _write_json(root / "run.json", manifest)
            _write(root / "README.md", _readme(manifest, files))
        return Result(value=manifest)
    except (RunFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
        error = exc.error if isinstance(exc, RunFailure) else RunError("artifact-write-failed", str(exc))
        if run.mode == "normal" and ownership_verified:
            cleanup = _cleanup(run)
            return Result(error=RunError(error.error_type, f"{error.message}; cleanup: {json.dumps(cleanup)}"))
        return Result(error=RunError(error.error_type, f"{error.message}; preserved run: {root}; temporary worktree: {temporary}"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    # SKILL.md calls prepare, publish and finish; init, worktree, brief and local are its parts, kept for tests.
    for command in ("init", "prepare"):
        create = commands.add_parser(command)
        for name in ("workspace", "skill-path", "ide", "model-id"):
            create.add_argument(f"--{name}", required=True)
        if command == "prepare":
            create.add_argument("--pr-url", required=True)
            create.add_argument("--gh-command", default="gh")
            create.add_argument("--codegraph-command", default="npx @colbymchenry/codegraph init")
            create.add_argument("--codegraph-timeout", type=float, default=600)
        else:
            create.add_argument("--project", required=True)
            create.add_argument("--pr-number", required=True, type=int)
            create.add_argument("--remote", default="origin")
        create.add_argument("--mode", default="normal")
        create.add_argument("--language", default="English")
        create.add_argument("--temp-root", default=str(Path(tempfile.gettempdir()).resolve()))
        create.add_argument("--eval-root", default=str(Path.home() / "cr-evals"))
    worktree = commands.add_parser("worktree")
    worktree.add_argument("--run-dir", required=True)
    worktree.add_argument("--source-sha", required=True)
    worktree.add_argument("--source-remote")
    render = commands.add_parser("brief")
    render.add_argument("--run-dir", required=True)
    render.add_argument("--codegraph-available", required=True, choices=["true", "false"])
    local = commands.add_parser("local")
    local.add_argument("--run-dir", required=True)
    local.add_argument("--source-remote")
    local.add_argument("--codegraph-command", default="npx @colbymchenry/codegraph init")
    local.add_argument("--codegraph-timeout", type=float, default=600)
    release = commands.add_parser("publish")
    release.add_argument("--run-dir", required=True)
    release.add_argument("--gh-command", default="gh")
    complete = commands.add_parser("finish")
    complete.add_argument("--run-dir", required=True)
    complete.add_argument("--status", required=True)
    complete.add_argument("--failed-stage")
    complete.add_argument("--error-type")
    complete.add_argument("--message")
    args = parser.parse_args()
    gh = shlex.split(getattr(args, "gh_command", None) or "gh")

    def clock() -> datetime:
        return datetime.now(timezone.utc)

    allocated: str | None = None  # a run_dir the agent must hand to finish after a failure
    mismatch = None
    if args.command == "prepare":
        parsed = parse_pr_url(args.pr_url)
        if parsed.value:
            args.project, args.pr_number = parsed.value
            remote = matching_remote(args.workspace, args.project)
            args.remote = remote.value
            mismatch = remote.error or gh_version(gh).error
        else:
            mismatch = parsed.error
    if mismatch:
        result: Result[object] = Result(error=mismatch)
    elif args.command in {"init", "prepare"}:
        created = initialize(workspace=args.workspace, skill_path=args.skill_path, project=args.project,
                             pr_number=args.pr_number, mode=args.mode, ide=args.ide, model_id=args.model_id,
                             language=args.language, temp_root=args.temp_root, eval_root=args.eval_root,
                             started_at=clock(), run_id=str(uuid.uuid4()), remote=args.remote)
        if created.value is None or args.command == "init":
            result = Result(value=asdict(created.value) if created.value else None, error=created.error)
        else:
            run, allocated = created.value, created.value.run_dir
            fetched = fetch_pr(run, gh, clock)
            staged = fetched if fetched.error else prepare_local(run, None, shlex.split(args.codegraph_command),
                                                                 args.codegraph_timeout, clock)
            # Only what the main agent binds; every printed byte is re-read on each later turn.
            identity = {key: asdict(run)[key] for key in ("run_id", "mode", "project", "pr_number", "run_dir", "skill_path")}
            result = Result(value={**identity, **staged.value} if staged.value else None, error=staged.error)
    else:
        loaded = load_run(args.run_dir)
        if loaded.value is None:
            result = Result(error=loaded.error)
        elif args.command == "worktree":
            prepared = prepare_worktree(loaded.value, args.source_sha, args.source_remote or loaded.value.remote)
            result = Result(value={"source_sha": prepared.value} if prepared.value else None, error=prepared.error)
        elif args.command == "brief":
            rendered = render_brief(loaded.value, args.codegraph_available == "true", clock())
            result = Result(value=rendered.value, error=rendered.error)
        elif args.command == "local":
            staged = prepare_local(loaded.value, args.source_remote, shlex.split(args.codegraph_command),
                                   args.codegraph_timeout, clock)
            result = Result(value=staged.value, error=staged.error)
        elif args.command == "publish":
            published = publish(loaded.value, gh, clock)
            result = Result(value=published.value, error=published.error)
        else:
            outcome = Outcome(args.status, args.failed_stage,
                              RunError(args.error_type, args.message or "") if args.error_type else None)
            completed = finish(loaded.value, outcome, clock())
            summary = dict(completed.value) if completed.value else None
            if summary is not None and "files" in summary:
                # The full index lives in run.json; stdout stays small for the agent context.
                summary["file_count"] = len(summary.pop("files"))  # type: ignore[arg-type]
            result = Result(value=summary, error=completed.error)
    if result.error:
        print(json.dumps({"success": False, **asdict(result.error), **({"run_dir": allocated} if allocated else {})}))
        return 1
    print(json.dumps(result.value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
