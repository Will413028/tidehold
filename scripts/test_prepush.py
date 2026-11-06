"""The lefthook pre-push hook must never start containers or a browser: the make targets it
runs may not reach `integration` (testcontainers) or `e2e` (Playwright, Docker). CI runs both
through `make ci`."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {"integration", "e2e", "ci", "dev-db"}


def prerequisites(makefile: str) -> dict[str, list[str]]:
    """Target -> prerequisites, from `target: deps` rule lines."""
    rules: dict[str, list[str]] = {}
    for line in makefile.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)(.*)$", line)
        if m and not m.group(1).startswith("."):
            rules.setdefault(m.group(1), []).extend(m.group(2).split())
    return rules


def reachable(rules: dict[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    todo = [start]
    while todo:
        target = todo.pop()
        if target not in seen:
            seen.add(target)
            todo.extend(rules.get(target, []))
    return seen


def prepush_make_targets(lefthook: str) -> list[str]:
    """Targets named by `make ...` run lines inside the pre-push block."""
    block = re.search(r"^pre-push:\n((?:[ \t#].*\n|\n)*)", lefthook, re.M)
    if block is None:
        raise AssertionError("lefthook.yml has no pre-push block")
    targets: list[str] = []
    for run in re.findall(r"^\s*run:\s*(.+)$", block.group(1), re.M):
        for cmd in re.findall(r"\bmake\s+([^;&|]+)", run):
            targets += [t for t in cmd.split() if not t.startswith("-") and "=" not in t]
    return targets


class PrePushTest(unittest.TestCase):
    def test_prepush_reaches_no_container_target(self) -> None:
        rules = prerequisites((ROOT / "Makefile").read_text())
        targets = prepush_make_targets((ROOT / "lefthook.yml").read_text())
        self.assertIn("check", targets)
        for target in targets:
            self.assertIn(target, rules, f"pre-push runs unknown make target {target}")
            hit = reachable(rules, target) & FORBIDDEN
            self.assertFalse(hit, f"pre-push `make {target}` reaches {sorted(hit)}")

    def test_ci_still_runs_integration_and_e2e(self) -> None:
        rules = prerequisites((ROOT / "Makefile").read_text())
        self.assertLessEqual({"integration", "e2e"}, reachable(rules, "ci"))
        self.assertRegex(
            (ROOT / ".github/workflows/ci.yml").read_text(), r"(?m)^\s*- run: make ci\s*$"
        )


if __name__ == "__main__":
    unittest.main()
