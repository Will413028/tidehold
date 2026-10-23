import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import quality_lock as ql

ZERO = dict.fromkeys(ql.SUPPRESSIONS, 0)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=ql.git_env()
    )


class SuppressionCountTest(unittest.TestCase):
    def test_each_kind_is_counted_once_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(
                "x = 1  # noqa: E501\n"
                "# ruff: noqa\n"
                "y: int = 'a'  # type: ignore[assignment]\n"
                "z = 1  # pyright: ignore[reportGeneralTypeIssues]\n"
            )
            (root / "b.ts").write_text(
                "// biome-ignore lint/style/noProcessEnv: test\n"
                "// @ts-expect-error\n"
                "// @ts-ignore\n"
                "/* eslint-disable */\n"
                "x() //nolint\n"
            )
            counts = ql.count_suppressions(root, ["a.py", "b.ts"])
        self.assertEqual(counts, dict.fromkeys(ql.SUPPRESSIONS, 1))

    def test_tracked_files_skip_the_pattern_definitions_and_lockfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            for rel in ["a.py", "uv.lock", "web/pnpm-lock.yaml", "README.md", ql.LOCK]:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("")
            git(root, "add", ".")
            self.assertEqual(ql.tracked_files(root), ["a.py"])


class GitEnvironmentTest(unittest.TestCase):
    def test_a_git_hooks_repository_is_not_touched(self):
        # lefthook in a linked worktree exports the pushing repository; these temporary
        # repositories must not write to it or read from it
        with tempfile.TemporaryDirectory() as hook_tmp, tempfile.TemporaryDirectory() as tmp:
            pushing, root = Path(hook_tmp), Path(tmp)
            git(pushing, "init", "-q")
            git(root, "init", "-q")
            (root / "a.py").write_text("")
            hook = {"GIT_DIR": str(pushing / ".git"), "GIT_INDEX_FILE": str(pushing / ".git/index")}
            with mock.patch.dict(os.environ, hook):
                git(root, "add", "a.py")
                (root / "a.py").unlink()
                self.assertEqual(ql.tracked_files(root), ["a.py"])
            self.assertEqual(ql.tracked_files(pushing), [])


class TestCountTest(unittest.TestCase):
    def test_skipped_tests_do_not_count_as_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            (reports / "backend.xml").write_text(
                '<testsuites><testsuite tests="5" skipped="1"/><testsuite tests="2"/></testsuites>'
            )
            (reports / "web.xml").write_text('<testsuite tests="3" skipped="0"/>')
            (reports / "recorder.txt").write_text("----\nRan 9 tests in 0.1s\n\nOK (skipped=2)\n")
            self.assertEqual(ql.count_tests(reports), {"backend": 6, "web": 3, "recorder": 7})

    def test_a_unittest_log_without_a_summary_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recorder.txt"
            path.write_text("ImportError\n")
            with self.assertRaises(ValueError):
                ql.executed_in_unittest(path)


LOCKED = {"suppressions": {**ZERO, "noqa": 2}, "tests": {"web": 3}}


class CompareTest(unittest.TestCase):
    def test_equal_counts_pass(self):
        self.assertEqual(ql.compare(LOCKED, LOCKED), [])

    def test_any_difference_fails_in_either_direction(self):
        for actual in [
            {"suppressions": {**ZERO, "noqa": 3}, "tests": {"web": 3}},
            {"suppressions": {**ZERO, "noqa": 1}, "tests": {"web": 3}},
            {"suppressions": {**ZERO, "noqa": 2}, "tests": {"web": 2}},
            {"suppressions": {**ZERO, "noqa": 2}, "tests": {"web": 4}},
            {"suppressions": {**ZERO, "noqa": 2}, "tests": {}},
            {"suppressions": {**ZERO, "noqa": 2}, "tests": {"web": 3, "new": 1}},
        ]:
            with self.subTest(actual=actual):
                self.assertEqual(len(ql.compare(LOCKED, actual)), 1)


class HistoryTest(unittest.TestCase):
    def commit(self, root: Path, lock: dict, message: str) -> None:
        (root / ql.LOCK).write_text(json.dumps(lock))
        git(root, "add", ql.LOCK)
        git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)

    def test_a_worse_lock_needs_a_reason_in_the_commit_message(self):
        base_lock = {"suppressions": {"noqa": 1}, "tests": {"web": 3}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            self.commit(root, base_lock, "base")
            git(root, "tag", "base")
            self.commit(root, {"suppressions": {"noqa": 0}, "tests": {"web": 4}}, "better")
            self.assertEqual(ql.history(root, "base"), [])

            self.commit(root, {"suppressions": {"noqa": 0}, "tests": {"web": 2}}, "drop a test")
            problems = ql.history(root, "base")
            self.assertEqual(len(problems), 1)
            self.assertIn("tests web lowered from 4 to 2", problems[0])

            git(
                root,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "--amend",
                "-m",
                "drop a test\n\nQuality-lock-reason: the feature it covered is gone",
            )
            self.assertEqual(ql.history(root, "base"), [])


if __name__ == "__main__":
    unittest.main()
