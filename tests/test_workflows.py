"""Workflow syntax and delivery contracts; no GitHub runs or SMTP actions invoked."""

import hashlib
import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    # BaseLoader preserves the GitHub key 'on', unlike YAML 1.1 boolean coercion.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {p.name: load(p) for p in (ROOT / ".github/workflows").glob("*.yml")}
        cls.main = cls.workflows["scrape_stats.yml"]

    def test_original_name_and_single_unchanged_weekly_schedule(self):
        self.assertEqual("Phrase AI Scraper", self.main["name"])
        schedules = [(name, s["cron"]) for name, w in self.workflows.items() for s in w["on"].get("schedule", [])]
        self.assertEqual([("scrape_stats.yml", "0 16 * * 0")], schedules)

    def test_manual_modes_default_to_usage_with_optional_dates(self):
        inputs = self.main["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(["usage", "leverage", "both"], inputs["mode"]["options"])
        self.assertEqual("usage", inputs["mode"]["default"])
        self.assertLessEqual(len(inputs), 10)
        for name in ("start", "end"):
            self.assertNotEqual("true", inputs[name].get("required"))

    def test_parallel_process_jobs_do_not_depend_on_each_other(self):
        jobs = self.main["jobs"]
        self.assertEqual("configure", jobs["build"]["needs"])
        self.assertEqual("configure", jobs["leverage"]["needs"])
        self.assertIn("outputs.usage", jobs["build"]["if"])
        self.assertIn("outputs.leverage", jobs["leverage"]["if"])
        self.assertEqual("always()", jobs["reporting_summary"]["if"])
        self.assertEqual({"configure", "build", "leverage"}, set(jobs["reporting_summary"]["needs"]))
        self.assertNotIn("continue-on-error", jobs["leverage"])

    def test_concurrency_and_timeouts_are_bounded(self):
        self.assertEqual("false", self.main["concurrency"]["cancel-in-progress"])
        self.assertIn("github.repository", self.main["concurrency"]["group"])
        for workflow in self.workflows.values():
            for job in workflow["jobs"].values():
                if "runs-on" in job:
                    self.assertTrue(0 < int(job["timeout-minutes"]) <= 60)

    def test_least_privilege_and_all_external_actions_use_full_sha(self):
        for workflow in self.workflows.values():
            self.assertEqual({"contents": "read"}, workflow["permissions"])
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
                        if step["uses"].startswith("actions/checkout@"):
                            self.assertEqual("false", step["with"]["persist-credentials"])
                    self.assertNotIn("${{ inputs.", step.get("run", ""))

    def test_original_email_metadata_and_attachments_are_byte_equivalent(self):
        # Canonical 'with' hashes captured from the pre-integration Git version.
        expected = {"Send Email with Zip Attachment": "72f0b4ec22890e28aada8d428c652954aab784a0c816c26adb851c6c5bc54ad5",
                    "Send Product Email": "4bc6638d1e775b7e9830174ea47796e6e6a76d68955de021f70f99f6a179909b"}
        actual = {step["name"]: hashlib.sha256(json.dumps(step["with"], sort_keys=True).encode()).hexdigest()
                  for step in self.main["jobs"]["build"]["steps"] if "action-send-mail@" in step.get("uses", "")}
        self.assertEqual(expected, actual)

    def test_existing_dashboard_and_smtp_secrets_stay_scoped_to_usage(self):
        steps = self.main["jobs"]["build"]["steps"]
        scraper = next(s for s in steps if s.get("id") == "scraper")
        self.assertEqual("python automation.py usage", scraper["run"])
        for name in ("PHRASE_API_URL", "PHRASE_REFERER", "PHRASE_API_URL_WORDS", "PHRASE_REFERER_WORDS"):
            self.assertEqual("${{ secrets." + name + " }}", scraper["env"][name])
        leverage = self.main["jobs"]["leverage"]
        extract = next(s for s in leverage["steps"] if s.get("id") == "extract")
        self.assertEqual("${{ secrets[needs.configure.outputs.credential_secret] }}", extract["env"]["PHRASE_TMS_AUTHORIZATION"])
        self.assertNotIn("EMAIL_PASSWORD", json.dumps(leverage))
        self.assertNotIn("PHRASE_REFERER", json.dumps(leverage))

    def test_usage_archives_keep_original_names_and_no_generated_data_is_committed(self):
        steps = self.main["jobs"]["build"]["steps"]
        scripts = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("zip Weekly_Phrase_Usage_Reports.zip", scripts)
        self.assertIn("zip Weekly_Phrase_SUSE_Cloud-Native_Usage_Reports.zip", scripts)
        self.assertIn("SUSE_Cloud-Native_Product_*.xlsx", scripts)
        self.assertNotIn("git commit", json.dumps(self.workflows))
        self.assertNotIn("git add", json.dumps(self.workflows))

    def test_leverage_artifacts_are_gated_and_contain_only_selected_outputs(self):
        upload = next(s for s in self.main["jobs"]["leverage"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertIn("outputs.upload_artifacts == 'true'", upload["if"])
        self.assertIn("!cancelled()", upload["if"])
        self.assertEqual("14", upload["with"]["retention-days"])
        self.assertIn("*Phrase_Leverage_Stats*.csv", upload["with"]["path"])
        self.assertIn("*Phrase_Leverage_Stats*.json", upload["with"]["path"])
        self.assertNotIn("snapshots.jsonl", upload["with"]["path"])
        self.assertNotIn("*.xlsx", upload["with"]["path"])

    def test_legacy_leverage_dispatch_reuses_execution_without_another_schedule(self):
        legacy = self.workflows["leverage_stats.yml"]
        self.assertNotIn("schedule", legacy["on"])
        job = legacy["jobs"]["reports"]
        self.assertEqual("./.github/workflows/scrape_stats.yml", job["uses"])
        self.assertEqual("leverage", job["with"]["mode"])
        self.assertEqual("inherit", job["secrets"])
        self.assertTrue(set(job["with"]).issubset(self.main["on"]["workflow_call"]["inputs"]))
        self.assertNotIn("concurrency", legacy)  # Avoid caller/callee concurrency deadlock.


if __name__ == "__main__":
    unittest.main()
