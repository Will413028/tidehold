"""Lock suppression comments (may only go down) and executed tests (may only go up).

`check` compares the tree and the test reports with `quality-lock.json`; any difference fails,
so an improvement is locked in by lowering (or raising) the value in the same change.
`history BASE` fails when a commit in BASE..HEAD moves a locked value the wrong way
without a `Quality-lock-reason:` line in its message.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = "quality-lock.json"
REASON = re.compile(r"^Quality-lock-reason: *\S", re.MULTILINE)

# Every way this repo's tools can be told to look away.
SUPPRESSIONS = {
    "noqa": re.compile(r"#\s*noqa\b", re.IGNORECASE),
    "ruff": re.compile(r"#\s*ruff\s*:\s*(noqa|ignore)\b", re.IGNORECASE),
    "type-ignore": re.compile(r"#\s*type\s*:\s*ignore\b"),
    "pyright-ignore": re.compile(r"#\s*pyright\s*:\s*ignore\b"),
    "nolint": re.compile(r"//\s*nolint\b"),
    "eslint-disable": re.compile(r"eslint-disable"),
    "biome-ignore": re.compile(r"biome-ignore"),
    "ts-expect-error": re.compile(r"@ts-expect-error"),
    "ts-ignore": re.compile(r"@ts-ignore"),
}
# Files that name the patterns rather than use them, and files no tool reads.
SKIP = {LOCK, "scripts/quality_lock.py", "scripts/test_quality_lock.py"}
SKIP_SUFFIXES = (".md", ".lock", "-lock.yaml")


def git_env() -> dict[str, str]:
    """The environment without the repository a git hook exports (`GIT_DIR`, `GIT_INDEX_FILE`,
    `GIT_WORK_TREE`, ...), so `git -C root` acts on `root`: lefthook runs in a linked worktree
    with them pointing at the pushing repository."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        # Untracked files too, so a new file is counted before it is committed.
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
        env=git_env(),
    ).stdout
    return [p for p in out.split("\0") if p and p not in SKIP and not p.endswith(SKIP_SUFFIXES)]


def count_suppressions(root: Path, paths: list[str]) -> dict[str, int]:
    counts = dict.fromkeys(SUPPRESSIONS, 0)
    for rel in paths:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for line in text.splitlines():
            for kind, pattern in SUPPRESSIONS.items():
                if pattern.search(line):
                    counts[kind] += 1
    return counts


def executed_in_junit(path: Path) -> int:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
    return sum(int(s.get("tests", 0)) - int(s.get("skipped", 0)) for s in suites)


def executed_in_unittest(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    ran = re.search(r"^Ran (\d+) tests? in ", text, re.MULTILINE)
    if ran is None:
        raise ValueError(f"{path}: no 'Ran N tests' line")
    skipped = re.search(r"skipped=(\d+)", text)
    return int(ran.group(1)) - (int(skipped.group(1)) if skipped else 0)


def count_tests(reports: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(reports.glob("*.xml")):
        counts[path.stem] = executed_in_junit(path)
    for path in sorted(reports.glob("*.txt")):
        counts[path.stem] = executed_in_unittest(path)
    return counts


def compare(lock: dict, actual: dict, not_run: frozenset[str] = frozenset()) -> list[str]:
    """Differences between the lock and this run; suites named in `not_run` are left to the run
    that executes them (pre-push leaves the browser tests to CI), and must have no report."""
    problems = []
    for kind in sorted(set(lock["suppressions"]) | set(actual["suppressions"])):
        want, got = lock["suppressions"].get(kind, 0), actual["suppressions"].get(kind, 0)
        if got > want:
            problems.append(f"suppressions {kind}: {got} > locked {want}; remove the new ones")
        elif got < want:
            problems.append(f"suppressions {kind}: {got} < locked {want}; lower the lock to {got}")
    for suite in sorted(not_run & set(actual["tests"])):
        problems.append(f"tests {suite}: reported although named as not run")
    for suite in sorted((set(lock["tests"]) | set(actual["tests"])) - not_run):
        want, got = lock["tests"].get(suite), actual["tests"].get(suite)
        if got is None:
            problems.append(f"tests {suite}: no report (locked {want}); did the suite run?")
        elif want is None:
            problems.append(f"tests {suite}: {got} executed but not locked; add it to the lock")
        elif got < want:
            problems.append(f"tests {suite}: {got} executed < locked {want}")
        elif got > want:
            problems.append(
                f"tests {suite}: {got} executed > locked {want}; raise the lock to {got}"
            )
    return problems


def worsened(before: dict, after: dict) -> list[str]:
    moves = []
    for kind, value in after["suppressions"].items():
        if value > before["suppressions"].get(kind, 0):
            moves.append(f"suppressions {kind} raised to {value}")
    for suite, value in before["tests"].items():
        if after["tests"].get(suite, 0) < value:
            moves.append(f"tests {suite} lowered from {value} to {after['tests'].get(suite, 0)}")
    return moves


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, env=git_env()
    )


def lock_at(root: Path, rev: str) -> dict | None:
    shown = git(root, "show", f"{rev}:{LOCK}")
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def history(root: Path, base: str) -> list[str]:
    if git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        base = "HEAD~1"  # unknown base (new branch, first push): check the last commit only
    revs = git(root, "rev-list", f"{base}..HEAD", "--", LOCK).stdout.split()
    problems = []
    for rev in revs:
        before, after = lock_at(root, f"{rev}^"), lock_at(root, rev)
        if before is None or after is None:
            continue
        moves = worsened(before, after)
        message = git(root, "log", "-1", "--format=%B", rev).stdout
        if moves and not REASON.search(message):
            problems.append(f"{rev[:10]}: {'; '.join(moves)} without a 'Quality-lock-reason:' line")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--reports", type=Path, required=True)
    check.add_argument("--not-run", action="append", default=[], metavar="SUITE")
    sub.add_parser("history").add_argument("base")
    args = parser.parse_args(argv)

    if args.command == "check":
        lock = json.loads((ROOT / LOCK).read_text(encoding="utf-8"))
        actual = {
            "suppressions": count_suppressions(ROOT, tracked_files(ROOT)),
            "tests": count_tests(args.reports),
        }
        problems = compare(lock, actual, frozenset(args.not_run))
    else:
        problems = history(ROOT, args.base)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
