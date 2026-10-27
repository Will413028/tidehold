"""The deploy's compose project (plan step 10a), as `docker compose config` resolves it: the
isolation, secrets by path and limits the design promises (step10a-design.md section 3).
Needs Docker, like the integration suite."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
# Each host env file holds one marker, so the resolved config shows which service gets which
# file; none holds a real value.
ENV_FILES = {
    "shared/paypal.env": "PAYPAL_CLIENT_ID",
    "tidehold/anthropic.env": "ANTHROPIC_API_KEY",
}


def resolved() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, key in ENV_FILES.items():
            (root / name).parent.mkdir(exist_ok=True)
            (root / name).write_text(f"{key}=marker\n")
        (root / "tidehold/db_password").write_text("marker\n")
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "tidehold",
                "-f",
                "compose.yaml",
                "--profile",
                "maintenance",
                "config",
                "--format",
                "json",
            ],
            cwd=DEPLOY,
            env={
                **os.environ,
                "TIDEHOLD_REVISION": "abc1234",
                "TIDEHOLD_ORIGIN": "https://tidehold.example.ts.net",
                "TIDEHOLD_SECRETS_DIR": str(root / "tidehold"),
                "SHARED_SECRETS_DIR": str(root / "shared"),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"docker compose config failed: {result.stderr}")
        project: dict[str, Any] = json.loads(result.stdout)
        project["secrets_root"] = str(root)
        return project


class ComposeProjectTest(unittest.TestCase):
    project: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = resolved()

    def test_the_project_is_isolated_and_publishes_no_port(self) -> None:
        services = self.project["services"]

        self.assertEqual(self.project["name"], "tidehold")
        self.assertEqual(
            set(services), {"db", "migrate", "ingest", "net", "api", "web", "ts", "audit"}
        )
        self.assertEqual([name for name, s in services.items() if s.get("ports")], [])
        self.assertEqual(
            {name for name, s in services.items() if s.get("network_mode")}, {"api", "web"}
        )

    def test_every_service_has_memory_and_cpu_limits(self) -> None:
        for name, service in self.project["services"].items():
            with self.subTest(name):
                self.assertLessEqual(int(service["mem_limit"]), 512 * 2**20)
                self.assertTrue(0 < float(service["cpus"]) <= 0.5)

    def test_secrets_come_from_host_files_and_only_where_needed(self) -> None:
        given = {
            name: {key for key in ENV_FILES.values() if key in (s.get("environment") or {})}
            for name, s in self.project["services"].items()
        }

        self.assertEqual(given.pop("api"), set(ENV_FILES.values()))
        self.assertEqual(given.pop("audit"), {"PAYPAL_CLIENT_ID"})  # no model keys
        self.assertEqual({name for name, keys in given.items() if keys}, set())
        self.assertEqual(
            self.project["secrets"]["db_password"]["file"],
            f"{self.project['secrets_root']}/tidehold/db_password",
        )
        for name in ("db", "migrate", "ingest", "api", "audit"):
            self.assertIn("db_password", str(self.project["services"][name]["secrets"]))

    def test_paypal_is_the_sandbox_wherever_it_is_configured(self) -> None:
        for name in ("api", "audit"):
            env = self.project["services"][name]["environment"]
            self.assertEqual(env["PAYPAL_ENV"], "sandbox", name)

    def test_the_recordings_are_mounted_read_only(self) -> None:
        (mount,) = self.project["services"]["ingest"]["volumes"]

        self.assertEqual(
            (mount["source"], mount["target"]), ("/var/lib/tidehold-recorder", "/recordings")
        )
        self.assertTrue(mount["read_only"])

    def test_the_reconciliation_log_survives_the_rebuild(self) -> None:
        script = (DEPLOY / "nightly.sh").read_text()

        self.assertEqual(re.findall(r"docker volume rm (\S+)", script), ["tidehold_db-data"])
        self.assertIn("reconcile-log", self.project["volumes"])

    def test_third_party_images_are_pinned_by_digest(self) -> None:
        images = [s["image"] for s in self.project["services"].values()]
        for dockerfile in ("backend.Dockerfile", "web.Dockerfile"):
            images += re.findall(r"^FROM (\S+)", (DEPLOY / dockerfile).read_text(), re.M)
        third_party = [i for i in images if not i.startswith("tidehold-")]

        self.assertEqual(len(third_party), 4)
        for image in third_party:
            self.assertRegex(image, r":[\w.-]+@sha256:[0-9a-f]{64}$")

    def test_the_nightly_job_avoids_the_fallbacks_hour(self) -> None:
        timer = (DEPLOY / "tidehold-nightly.timer").read_text()
        (calendar,) = re.findall(r"^OnCalendar=(.+)$", timer, re.M)

        self.assertEqual(calendar, "*-*-* 11:00:00 UTC")


if __name__ == "__main__":
    unittest.main()
