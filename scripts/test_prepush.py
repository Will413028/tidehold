"""The lefthook pre-push hook must never start containers or a browser: the make targets it
runs may not reach `integration` (testcontainers) or `e2e` (Playwright, Docker), and no pytest
run they reach may collect a test under backend/tests/integration or backend/tests/e2e. CI runs
both through `make ci`.

Limitation: `make -C <dir>` / `make -f <file>` calls into another directory or makefile are not
followed; their targets are not checked against the root Makefile's rules."""

import re
import shlex
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {"integration", "e2e", "ci", "dev-db"}
CONTAINER_SUITES = (ROOT / "backend/tests/integration", ROOT / "backend/tests/e2e")

MAKE_CALL = re.compile(r"(?:\$\(MAKE\)|\$\{MAKE\}|\bmake)\s+([^;&|]+)")
# make options whose value is the next word (or attached: -Cdir, --directory=dir).
VALUE_OPTS = {
    "-C",
    "-f",
    "-I",
    "-o",
    "-W",
    "--directory",
    "--file",
    "--makefile",
    "--include-dir",
    "--old-file",
    "--assume-old",
    "--what-if",
    "--new-file",
    "--assume-new",
}
ELSEWHERE_OPTS = ("-C", "-f", "--directory", "--file", "--makefile")


def make_args(cmd: str) -> list[str]:
    """Root-Makefile targets named by a make command line; [] when -C/-f points it at another
    directory or makefile (those targets are not root rules)."""
    tokens = cmd.split()
    targets: list[str] = []
    elsewhere = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        opt, value = tok, None
        if tok.startswith("--") and "=" in tok:
            opt, value = tok.split("=", 1)
        elif re.fullmatch(r"-[CfIoW].+", tok):
            opt, value = tok[:2], tok[2:]
        if opt in VALUE_OPTS:
            if value is None:
                i += 1
                value = tokens[i] if i < len(tokens) else ""
            if opt in ELSEWHERE_OPTS and value not in {".", "./", "$(CURDIR)"}:
                elsewhere = True
        elif tok in {"-j", "-l"} and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            i += 1
        elif not tok.startswith("-") and "=" not in tok:
            targets.append(tok)
        i += 1
    return [] if elsewhere else targets


def parse(makefile: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Target -> prerequisites plus targets run by `$(MAKE) x` / `make x` in its recipe,
    and target -> recipe commands (backslash continuations joined)."""
    rules: dict[str, list[str]] = {}
    recipes: dict[str, list[str]] = {}
    current: str | None = None
    pending = ""
    for line in makefile.splitlines():
        if (line.startswith("\t") or pending) and current is not None:
            pending += line.strip()
            if pending.endswith("\\"):
                pending = pending[:-1] + " "
                continue
            recipes[current].append(pending)
            for cmd in MAKE_CALL.findall(pending):
                rules[current].extend(make_args(cmd))
            pending = ""
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


def reachable(rules: dict[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    todo = [start]
    while todo:
        target = todo.pop()
        if target not in seen:
            seen.add(target)
            todo.extend(rules.get(target, []))
    return seen


def pytest_invocations(makefile: str, targets: list[str]) -> list[tuple[Path, list[str]]]:
    """(working directory, pytest arguments) for each pytest command reachable from
    `targets`; report-file options are dropped since collection writes no report."""
    rules, recipes = parse(makefile)
    seen: set[str] = set()
    for target in targets:
        seen |= reachable(rules, target)
    found = []
    for target in sorted(seen):
        for command in recipes.get(target, []):
            cwd = ROOT
            for part in re.split(r"&&|;", command):
                words = shlex.split(part.replace("$(REPORTS)", "/dev/null"))
                if words[:1] == ["cd"] and len(words) == 2:
                    cwd = ROOT / words[1]
                elif "pytest" in words:
                    args = words[words.index("pytest") + 1 :]
                    found.append((cwd, [a for a in args if not a.startswith("--junitxml")]))
    return found


def collected_container_tests(cwd: Path, args: list[str]) -> list[str]:
    """Node ids pytest collects with `args` in `cwd` that live under a container suite.
    Collection only imports test modules; the containers start in fixtures at run time.
    Verbosity flags are dropped (one -q lists node ids) and --rootdir=/ makes ids absolute."""
    args = [a for a in args if not re.fullmatch(r"-[qv]+|--quiet|--verbose", a)]
    run = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "pytest",
            *args,
            "--collect-only",
            "-q",
            "--rootdir=/",
            "-p",
            "no:cacheprovider",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    nodes = [line for line in run.stdout.splitlines() if "::" in line]
    if run.returncode != 0 or not nodes:
        raise AssertionError(f"collection failed in {cwd} {args}:\n{run.stdout}{run.stderr}")
    return [
        node
        for node in nodes
        if any(Path("/", node.split("::")[0]).resolve().is_relative_to(s) for s in CONTAINER_SUITES)
    ]


class PrePushTest(unittest.TestCase):
    def test_prepush_reaches_no_container_target(self) -> None:
        rules = prerequisites((ROOT / "Makefile").read_text())
        targets = prepush_make_targets((ROOT / "lefthook.yml").read_text())
        self.assertIn("check", targets)
        for target in targets:
            self.assertIn(target, rules, f"pre-push runs unknown make target {target}")
            hit = reachable(rules, target) & FORBIDDEN
            self.assertFalse(hit, f"pre-push `make {target}` reaches {sorted(hit)}")

    def test_prepush_pytest_collects_no_container_suite(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        targets = prepush_make_targets((ROOT / "lefthook.yml").read_text())
        runs = pytest_invocations(makefile, targets)
        self.assertIn(ROOT / "backend", [cwd for cwd, _ in runs])
        for cwd, args in runs:
            with self.subTest(cwd=str(cwd), args=args):
                self.assertEqual(collected_container_tests(cwd, args), [])

    def test_guard_flags_widened_pytest(self) -> None:
        for cmd in ("uv run --frozen pytest -q", "pytest tests", "pytest ."):
            with self.subTest(cmd=cmd):
                makefile = f"check: unit\nunit:\n\tcd backend && {cmd} --junitxml=$(REPORTS)/x\n"
                [(cwd, args)] = pytest_invocations(makefile, ["check"])
                self.assertTrue(collected_container_tests(cwd, args))

    def test_guard_follows_recipe_make_calls(self) -> None:
        for call in ("$(MAKE) integration", "${MAKE} e2e", "make -C . ci", "make -j 4 dev-db"):
            rules = prerequisites(f"check: unit\nunit:\n\tpytest x\n\t{call}\n")
            self.assertTrue(reachable(rules, "check") & FORBIDDEN, call)
        rules = prerequisites("check: unit\nunit:\n\tpytest x\nintegration:\n\tpytest y\n")
        self.assertFalse(reachable(rules, "check") & FORBIDDEN)

    def test_make_args_skips_other_makefiles_and_option_values(self) -> None:
        for cmd in (
            "-C web e2e",
            "-Cweb e2e",
            "--directory=web e2e",
            "-f other.mk integration",
            "--file other.mk integration",
        ):
            self.assertEqual(make_args(cmd), [], cmd)
        self.assertEqual(make_args("-j 4 -C . check security"), ["check", "security"])

    def test_ci_still_runs_integration_and_e2e(self) -> None:
        rules = prerequisites((ROOT / "Makefile").read_text())
        self.assertLessEqual({"integration", "e2e"}, reachable(rules, "ci"))
        self.assertRegex(
            (ROOT / ".github/workflows/ci.yml").read_text(), r"(?m)^\s*- run: make ci\s*$"
        )


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


if __name__ == "__main__":
    unittest.main()
