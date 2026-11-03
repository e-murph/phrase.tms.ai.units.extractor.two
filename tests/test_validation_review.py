"""Independent accuracy and hostile-contract regressions found in the review."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from leverage.client import ApiError, TmsClient
from leverage.config import Settings
from leverage.export import export
from leverage.extractor import Extraction
from leverage.reporting import Reports
from tests.fakes import FakeSession, PhraseFixture, response


class ValidationReviewTests(unittest.TestCase):
    def setUp(self):
        self.api = PhraseFixture()

    def extract(self, **options):
        config = dict(authorization="ApiToken synthetic-review", start="2026-01-01",
                      end="2027-01-01", download_json=False)
        config.update(options)
        self.session = FakeSession(self.api)
        self.extraction = Extraction(TmsClient(Settings(**config), self.session, Mock(), lambda: 0)).run()
        self.reports = Reports(self.extraction)
        return self.reports

    def german(self):
        return next(r for r in self.reports.projects if r["project_uid"] == "P1" and
                    r["target_language"] == "de" and r["workflow_level"] == 1)

    def test_estimated_discounted_counts_are_preserved_but_not_final_savings(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["discountedData"]["estimate"] = True
        self.extract()
        row = self.german()
        self.assertEqual(100, row["total_gross_words"])
        self.assertIsNone(row["discounted_words"])
        self.assertIsNone(row["saved_words"])
        detail = next(r for r in self.reports.details if r["analysis_uid"] == "A2" and
                      r["job_uid"] == "J1" and r["match_category"] == "match100" and r["match_origin"] == "TM")
        self.assertEqual(10, detail["discounted_words"])
        self.assertIsNone(detail["saved_words"])
        self.assertEqual("discounted_estimated", detail["data_status"])

    def test_estimated_raw_details_never_advertise_valid_savings(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["data"]["estimate"] = True
        self.extract()
        detail = [r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1"]
        self.assertTrue(detail)
        self.assertTrue(all(r["saved_words"] is None and not r["selected_for_summary"] for r in detail))

    def test_offsetting_category_errors_cannot_hide_behind_matching_totals(self):
        member = self.api.analyses["A2"]["members"]["2"][0]
        for kind in ("data", "discountedData"):
            with self.subTest(kind=kind):
                original = deepcopy(member[kind])
                for unit, delta in (("words", 1), ("characters", 5), ("segments", Decimal("0.1"))):
                    member[kind]["transMemoryMatches"]["match100"][unit] = Decimal(str(member[kind]["transMemoryMatches"]["match100"][unit])) + delta
                    member[kind]["repetitions"][unit] = Decimal(str(member[kind]["repetitions"][unit])) - delta
                self.extract()
                checks = [c for c in self.reports.validation.checks if c["check"] == "job_membership_category" and
                          c["analysis_uid"] == "A2" and c["count_kind"] == ("raw" if kind == "data" else "discounted")]
                self.assertTrue(any(c["status"] == "failed" for c in checks))
                self.assertIsNone(self.german()["total_gross_words" if kind == "data" else "discounted_words"])
                member[kind] = original

    def test_nt_different_api_bands_are_reconciled_at_origin_total(self):
        self.extract()
        checks = [c for c in self.reports.validation.checks if c["check"] == "job_membership_category" and
                  c.get("origin_key") == "nonTranslatablesMatches"]
        self.assertTrue(checks)
        self.assertTrue(all(c["status"] == "passed" and c["resolution"] == "origin_total" for c in checks))

    def test_job_identity_project_and_workflow_disagreements_are_excluded(self):
        mutations = (
            lambda job: job.update(uid="WRONG"),
            lambda job: job["project"].update(uid="P2"),
            lambda job: job["workflowStep"].update(workflowLevel=2),
            lambda job: job.update(workflowLevel=2),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.api = PhraseFixture()
                job = deepcopy(self.api.jobs["J1"])
                mutate(job)
                self.api.overrides["/api2/v1/projects/P1/jobs/J1"] = (200, job)
                self.extract()
                self.assertIsNone(self.german()["total_gross_words"])
                self.assertFalse(any(r["job_uid"] == "J1" and r["selected_for_summary"] for r in self.reports.details))
                self.assertTrue(any(r["project_uid"] == "P1" and r["target_language"] == "fr" and
                                    r["total_gross_words"] == 200 for r in self.reports.projects))

    def test_malformed_nested_metadata_does_not_abort_unrelated_projects(self):
        mutations = (
            lambda f: f.analyses["A2"]["body"].update(importStatus=["OK"]),
            lambda f: f.analyses["A2"]["body"]["analyseLanguageParts"][0].update(jobs=42),
            lambda f: f.jobs["J1"].update(targetLang=["de"]),
            lambda f: f.jobs["J1"].update(jobReference=["SOURCE"]),
            lambda f: f.overrides.update({"/api2/v3/analyses/A2": (200, {**f.analyses["A2"]["body"], "project": ["P1"]})}),
            lambda f: f.analyses["A2"]["body"].update(type=["PreAnalyse"]),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.api = PhraseFixture()
                mutate(self.api)
                self.extract()
                self.assertIn("P2", self.extraction.tables["projects"])
                self.assertTrue(any(r["impact"] == "core" for r in self.extraction.tables["coverage"].values()))
                with tempfile.TemporaryDirectory() as output:
                    manifest = export(self.extraction, output)
                    self.assertFalse(manifest["core_complete"])
                    self.assertTrue((Path(output) / manifest["report_files"]["structured_json"]).exists())

    def test_duplicate_language_ids_are_diagnosed_and_originals_retained(self):
        body = self.api.analyses["A2"]["body"]
        body["analyseLanguageParts"].append(deepcopy(body["analyseLanguageParts"][0]))
        self.extract()
        self.assertTrue(any(r["code"] == "DUPLICATE_LANGUAGE_PART" for r in self.extraction.tables["coverage"].values()))
        self.assertIsNone(self.german()["total_gross_words"])
        original = next(a for a in self.reports.structured()["analysis_data"] if a["metadata"]["analysis_uid"] == "A2")
        self.assertEqual(3, len(original["language_parts"]))

    def test_incomplete_client_coverage_is_not_labelled_complete(self):
        self.extract()
        with tempfile.TemporaryDirectory() as output:
            manifest = export(self.extraction, output)
        self.assertTrue(manifest["core_complete"])
        self.assertFalse(manifest["client_reporting_complete"])
        self.assertEqual("partial", manifest["status"])

    def test_known_split_without_bounds_cannot_claim_complete_volume(self):
        self.api.jobs["J1"].update(isParentJobSplit=True, endIndex=None)
        self.extract()
        self.assertIsNone(self.german()["total_gross_words"])
        self.assertTrue(any(i["code"] == "SPLIT_RANGE_UNKNOWN" for i in self.extraction.tables["coverage"].values()))

    def test_job_inventory_explicitly_requests_pagination_counts(self):
        self.extract()
        calls = [kwargs["params"] for path, kwargs in self.session.calls if path.startswith("/api2/v2/projects/") and path.endswith("/jobs")]
        self.assertTrue(calls)
        self.assertTrue(all(p["count"] == "true" for p in calls))

    def test_malformed_display_and_client_fields_preserve_other_reports(self):
        self.api.projects["P1"].update(name={"unexpected": "object"})
        self.api.projects["P1"]["client"].update(uid=["bad"], name=["bad"])
        self.extract()
        self.assertEqual(100, self.german()["total_gross_words"])
        self.assertTrue(all(r["client_uid"] is None for r in self.reports.clients))
        self.assertTrue(any(i["code"] == "CLIENT_SCHEMA" for i in self.extraction.tables["coverage"].values()))

    def test_missing_all_control_does_not_crash_membership_validation(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["data"]["all"] = {}
        self.extract()
        self.assertIsNone(self.german()["total_gross_words"])
        self.assertFalse(self.reports.validation.summary()["valid"])
        with tempfile.TemporaryDirectory() as output:
            self.assertFalse(export(self.extraction, output)["core_complete"])

    def test_malformed_parts_array_survives_as_unparsed_json_evidence(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"] = 42
        self.extract()
        original = next(a for a in self.reports.structured()["analysis_data"] if a["metadata"]["analysis_uid"] == "A2")
        self.assertEqual(42, original["unparsed_language_parts"])
        with tempfile.TemporaryDirectory() as output:
            self.assertFalse(export(self.extraction, output)["core_complete"])

    def test_malformed_scheme_metadata_preserves_raw_without_fabricating_net(self):
        self.api.analyses["A2"]["body"]["netRateScheme"] = ["malformed"]
        self.extract()
        self.assertEqual(100, self.german()["total_gross_words"])
        self.assertIsNone(self.german()["discounted_words"])
        self.assertTrue(any(r["job_uid"] == "J1" and r["data_status"] == "discounted_unavailable" for r in self.reports.details))
        with tempfile.TemporaryDirectory() as output:
            self.assertEqual("partial", export(self.extraction, output)["status"])

    def test_malformed_scheme_identity_cannot_establish_discount_association(self):
        for value in ({"uid": ["S1"]}, {"id": True}, {"uid": " ", "id": ""}):
            with self.subTest(scheme=value):
                self.api.analyses["A2"]["body"]["netRateScheme"] = value
                self.extract()
                self.assertEqual(100, self.german()["total_gross_words"])
                self.assertIsNone(self.german()["discounted_words"])
                self.assertIsNone(self.german()["saved_words"])

    def test_empty_client_identifiers_do_not_merge_unrelated_projects(self):
        for project in self.api.projects.values():
            project["client"] = {"uid": "", "id": "", "name": "Unnamed client"}
        self.extract()
        german = [r for r in self.reports.clients if r["target_language"] == "de"]
        self.assertEqual(2, len(german))
        self.assertTrue(all(not r["client_identity_resolved"] and r["number_of_projects"] == 1 for r in german))

    def test_all_retryable_server_statuses_are_bounded(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                client = TmsClient(Settings(authorization="ApiToken synthetic-review", retries=2),
                                   FakeSession(lambda p, q: response(status, {})), Mock(), lambda: 0)
                with self.assertRaises(ApiError) as error:
                    client.get("/api2/v1/projects")
                self.assertEqual(status, error.exception.status)
                self.assertEqual((3, 3), (client.request_count, client.failed_request_count))

    def test_nonstandard_json_numbers_are_safe_schema_errors(self):
        for value in (b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(value=value):
                client = TmsClient(Settings(authorization="ApiToken synthetic-review"),
                                   FakeSession(lambda p, q: response(200, content=b'{"words":' + value + b'}')),
                                   Mock(), lambda: 0)
                with self.assertRaises(ApiError) as error:
                    client.get("/api2/v1/projects")
                self.assertEqual("INVALID_JSON", error.exception.code)


if __name__ == "__main__":
    unittest.main()
