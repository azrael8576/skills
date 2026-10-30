"""Stand-in for `gh`: answers `--version` and `gh api` from a JSON fixture and logs every call.

Usage: python fake_gh.py <fixture.json> <gh arguments...>
Fixture: {"version": "...", "routes": {endpoint: response | [response per call]}}
Response: {"json": value} or {"exit": code, "stderr": text}.
"""

import json
import sys
from pathlib import Path

VALUE_FLAGS = {"--jq", "--method", "-F", "--hostname"}


def main() -> int:
    fixture_path, args = Path(sys.argv[1]), sys.argv[2:]
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    log = fixture_path.with_suffix(".calls.jsonl")
    previous = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    with log.open("a") as handle:
        handle.write(json.dumps(args) + "\n")
    if args == ["--version"]:
        print(fixture["version"])
        return 0
    endpoint, options, rest = None, {}, iter(args[1:])
    for arg in rest:
        if arg in VALUE_FLAGS:
            options.setdefault(arg, []).append(next(rest))
        elif not arg.startswith("-"):
            endpoint = arg
    route = fixture["routes"][endpoint]
    if isinstance(route, list):
        calls = sum(1 for call in previous if endpoint in call)
        route = route[min(calls, len(route) - 1)]
    if "exit" in route:
        print(route.get("stderr", ""), file=sys.stderr)
        return int(route["exit"])
    for field in options.get("-F", []):
        name, _, value = field.partition("=")
        if name == "body" and value.startswith("@"):
            fixture_path.with_suffix(".posted").write_bytes(Path(value[1:]).read_bytes())
    value = route["json"]
    if "--jq" in options:
        for key in options["--jq"][0].strip(".").split("."):
            value = value[key]
        print(value if isinstance(value, str) else json.dumps(value))
    else:
        print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
