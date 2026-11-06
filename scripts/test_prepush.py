"""The lefthook pre-push hook must never start containers or a browser: the make targets it
runs may not reach `integration` (testcontainers) or `e2e` (Playwright, Docker). CI runs both
through `make ci`."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {"integration", "e2e", "ci", "dev-db"}


MAKE_CALL = re.compile(r"(?:\$\(MAKE\)|\$\{MAKE\}|\bmake)\s+([^;&|]+)")


def make_args(cmd: str) -> list[str]:
    return [t for t in cmd.split() if not t.startswith("-") and "=" not in t]


def parse(makefile: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Target -> prerequisites plus targets run by `$(MAKE) x` / `make x` in its recipe,
    and target -> recipe lines."""
    rules: dict[str, list[str]] = {}
    recipes: dict[str, list[str]] = {}
    current: str | None = None
    for line in makefile.splitlines():
        if line.startswith("\t") and current is not None:
            recipes[current].append(line)
            for cmd in MAKE_CALL.findall(line):
                rules[current].extend(make_args(cmd))
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)(.*)$", line)
        if m and not m.group(1).startswith("."):
            current = m.group(1)
            rules.setdefault(current, []).extend(m.group(2).split())
            recipes.setdefault(current, [])
        elif line and not line.startswith("#"):
            current = None
    return rules, recipes


def prerequisites(makefile: str) -> dict[str, list[str]]:
    return parse(makefile)[0]


def unexcluded_pytest(makefile: str, targets: list[str]) -> list[str]:
    """pytest lines over backend `tests/` reachable from `targets` that do not exclude the
    container suites (tests/integration and tests/e2e are told apart by path only)."""
    rules, recipes = parse(makefile)
    seen: set[str] = set()
    for target in targets:
        seen |= reachable(rules, target)
    return [
        line.strip()
        for target in sorted(seen)
        for line in recipes.get(target, [])
        if "pytest" in line
        and re.search(r"(?<![./\w])tests\b", line)
        and not ("--ignore=tests/integration" in line and "--ignore=tests/e2e" in line)
    ]


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
            targets += make_args(cmd)
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

    def test_prepush_pytest_excludes_container_suites(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        targets = prepush_make_targets((ROOT / "lefthook.yml").read_text())
        self.assertEqual(unexcluded_pytest(makefile, targets), [])

    def test_guard_follows_recipe_make_calls(self) -> None:
        for call in ("$(MAKE) integration", "${MAKE} e2e", "make -C . ci"):
            rules = prerequisites(f"check: unit\nunit:\n\tpytest x\n\t{call}\n")
            self.assertTrue(reachable(rules, "check") & FORBIDDEN, call)
        rules = prerequisites("check: unit\nunit:\n\tpytest x\nintegration:\n\tpytest y\n")
        self.assertFalse(reachable(rules, "check") & FORBIDDEN)

    def test_guard_flags_widened_pytest(self) -> None:
        bad = "check: unit\nunit:\n\tcd backend && pytest -q tests\n"
        self.assertEqual(unexcluded_pytest(bad, ["check"]), ["cd backend && pytest -q tests"])
        good = (
            "check: unit\nunit:\n\tpytest -q tests --ignore=tests/integration --ignore=tests/e2e\n"
        )
        self.assertEqual(unexcluded_pytest(good, ["check"]), [])

    def test_ci_still_runs_integration_and_e2e(self) -> None:
        rules = prerequisites((ROOT / "Makefile").read_text())
        self.assertLessEqual({"integration", "e2e"}, reachable(rules, "ci"))
        self.assertRegex(
            (ROOT / ".github/workflows/ci.yml").read_text(), r"(?m)^\s*- run: make ci\s*$"
        )


if __name__ == "__main__":
    unittest.main()
