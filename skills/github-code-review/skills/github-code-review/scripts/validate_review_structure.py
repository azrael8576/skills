#!/usr/bin/env python3
"""Validate github-code-review SubAgent markdown structure before posting."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CODEGRAPH_FOOTER = "used CodeGraph !"


def _read_review_text(review_file: str | None) -> str:
    if review_file and review_file != "/dev/stdin":
        return Path(review_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def _non_empty_lines(text: str) -> list[str]:
    return [line.rstrip("\n") for line in text.splitlines() if line.strip()]


def validate_review_structure(
    review_text: str,
    *,
    pr_number: str,
    source_branch: str,
    target_branch: str,
    author: str,
    model_id: str,
    timestamp: str,
    require_codegraph_marker: bool = False,
) -> dict:
    failures: list[dict[str, str]] = []
    lines = _non_empty_lines(review_text)

    expected_header = f"### 🤖 AI Code Review — #{pr_number}"
    if not lines:
        failures.append(
            {
                "check": "header",
                "code": "header_missing",
                "message": f"Review is empty; expected header: {expected_header}",
            }
        )
    elif lines[0] != expected_header:
        failures.append(
            {
                "check": "header",
                "code": "header_mismatch",
                "message": (
                    f"First line must be exactly: {expected_header!r}; "
                    f"got: {lines[0]!r}"
                ),
            }
        )

    author_handle = author.lstrip("@")
    expected_branch = (
        f"`{source_branch}` → `{target_branch}` · @{author_handle}"
    )
    branch_pattern = re.compile(
        rf"^`{re.escape(source_branch)}` → `{re.escape(target_branch)}` · @"
        rf"{re.escape(author_handle)}$"
    )
    if len(lines) < 2 or not branch_pattern.match(lines[1]):
        failures.append(
            {
                "check": "branch_info",
                "code": "branch_info_missing",
                "message": (
                    "Missing required branch info line immediately after header: "
                    f"{expected_branch!r}"
                ),
            }
        )

    verdict_found = any(line.startswith("**Verdict") for line in lines)
    if not verdict_found:
        failures.append(
            {
                "check": "verdict",
                "code": "verdict_missing",
                "message": "Missing verdict line starting with '**Verdict'",
            }
        )

    expected_signature = f"*{model_id} · {timestamp} UTC*"
    signature_index = len(lines) - (2 if require_codegraph_marker else 1)
    if signature_index < 0 or lines[signature_index] != expected_signature:
        failures.append(
            {
                "check": "signature",
                "code": "signature_mismatch",
                "message": (
                    "Signature must be the final line (or directly before the "
                    "Codegraph footer) and exactly equal to: "
                    f"{expected_signature!r}"
                ),
            }
        )

    if require_codegraph_marker:
        marker_indexes = [
            i for i, line in enumerate(lines) if line == CODEGRAPH_FOOTER
        ]
        if not marker_indexes:
            failures.append(
                {
                    "check": "codegraph_footer",
                    "code": "codegraph_footer_missing",
                    "message": (
                        "Missing required Codegraph footer line after signature: "
                        f"{CODEGRAPH_FOOTER!r}"
                    ),
                }
            )
        elif marker_indexes != [len(lines) - 1]:
            failures.append(
                {
                    "check": "codegraph_footer",
                    "code": "codegraph_footer_order",
                    "message": (
                        "Codegraph footer must be the final line, directly below "
                        f"the signature ({CODEGRAPH_FOOTER!r})"
                    ),
                }
            )

    return {
        "ok": len(failures) == 0,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate github-code-review markdown structure."
    )
    parser.add_argument(
        "--review-file",
        default="/dev/stdin",
        help="Path to review markdown (default: stdin).",
    )
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--source", required=True, dest="source_branch")
    parser.add_argument("--target", required=True, dest="target_branch")
    parser.add_argument("--author", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument(
        "--require-codegraph-marker",
        action="store_true",
        help=(
            "Require a line exactly equal to "
            f"{CODEGRAPH_FOOTER!r} immediately after the signature "
            "(treatment skill)."
        ),
    )
    args = parser.parse_args()

    review_text = _read_review_text(args.review_file)
    result = validate_review_structure(
        review_text,
        pr_number=args.pr_number,
        source_branch=args.source_branch,
        target_branch=args.target_branch,
        author=args.author,
        model_id=args.model_id,
        timestamp=args.timestamp,
        require_codegraph_marker=args.require_codegraph_marker,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
