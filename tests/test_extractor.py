from decimal import Decimal
import unittest
from unittest.mock import Mock

from leverage.client import TmsClient
from leverage.config import Settings
from leverage.extractor import Extraction
from leverage.numeric import dumps
from tests.fakes import PhraseFixture, response


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self.api = PhraseFixture()

    def run_fixture(self, **kwargs):
        options = {"authorization": "ApiToken fixture-secret", "start": "2026-01-01", "end": "2027-01-01", "retries": 0}
        options.update(kwargs)
        from tests.fakes import FakeSession
        self.session = FakeSession(self.api)
        self.client = TmsClient(Settings(**options), self.session, Mock(), lambda: 0)
        self.result = Extraction(self.client, {"Product German": "Mapped product"}).run()
        return self.result

    def selected(self):
        return {row["job_uid"]: row for row in self.result.tables["selected_jobs"].values()}

    def test_multilingual_multi_job_selection_and_workflows(self):
        result = self.run_fixture()
        self.assertTrue(result.manifest()["core_complete"])
        self.assertEqual(4, len(result.tables["analyses"]))
        self.assertEqual(5, len(result.tables["language_totals"]))
        self.assertEqual(6, len(result.tables["job_analyses"]))
        selected = self.selected()
        self.assertEqual({"J1", "J2", "J3", "J4"}, set(selected))
        self.assertEqual("A2", selected["J1"]["analysis_uid"])
        self.assertEqual("A1", selected["J2"]["analysis_uid"])
        self.assertEqual(2, selected["J2"]["workflow_level"])
        self.assertEqual("SOURCE12", selected["J1"]["source_job_uid"])
        self.assertEqual("SOURCE12", selected["J2"]["source_job_uid"])
        self.assertEqual("fr", selected["J3"]["target_language"])
        self.assertEqual(420, sum(row["gross_words"] for row in selected.values()))
        self.assertTrue(all(row["count_basis"] == "source" for row in selected.values()))
        self.assertEqual(1, sum(p == "/api2/v3/analyses/A2" for p, _ in self.session.calls))
        self.assertEqual(1, sum(p == "/api2/v2/netRateSchemes/S1" for p, _ in self.session.calls))
        self.assertIn("PostAnalyse", {a["analysis_type"] for a in result.tables["analyses"].values()})
        self.assertFalse(any(r["analysis_type"] == "PostAnalyse" for r in selected.values()))

    def test_direct_counts_calculated_savings_and_metadata(self):
        self.run_fixture()
        row = self.selected()["J1"]
        for key, value in {"gross_words": 100, "discounted_words": 50, "word_savings": 50,
                           "word_savings_percent": 50, "raw_characters": 500, "raw_segments": 10,
                           "total_source_words": 100, "client_name": "Client Seven", "product_name": "Mapped product"}.items():
            self.assertEqual(value, row[key])
        self.assertIsNone(row["analysis_updated_at"])
        self.assertIsNone(row["analysis_updated_at_field"])
        categories = {m["category"] for m in self.result.tables["measures"].values()}
        self.assertTrue({"all", "transMemoryMatches", "machineTranslationMatches", "repetitions",
                         "internalFuzzyMatches", "nonTranslatablesMatches", "futureMatchCategory"}.issubset(categories))
        measure = next(m for m in self.result.tables["measures"].values()
                       if m.get("job_uid") == "J1" and m["analysis_uid"] == "A2" and m["field_path"] == "transMemoryMatches.match100.words")
        self.assertEqual((20, 10, 10, True), (measure["raw_value"], measure["discounted_value"], measure["word_savings"], measure["selected"]))

    def test_nt_version_difference_and_native_json_are_preserved(self):
        self.run_fixture()
        nt = [m for m in self.result.tables["measures"].values() if m["category"] == "nonTranslatablesMatches"]
        self.assertTrue(any(m["grain"] == "analysis_language" and m["band"] == "match95" for m in nt))
        self.assertTrue(any(m["grain"] == "analysis_job" and m["band"] == "match99" for m in nt))
        downloads = [s for s in self.client.snapshots.values() if s["kind"] == "native_download"]
        self.assertEqual(4, len(downloads))
        self.assertEqual("v1-export", downloads[0]["endpoint_version"])
        self.assertEqual(100, self.selected()["J1"]["gross_words"])  # not 12.5 from the differently shaped download
        self.assertNotIn("csrfToken", dumps(list(self.client.snapshots.values())))

    def test_no_analysis_and_missing_scheme_never_manufacture_zeros(self):
        self.run_fixture()
        codes = {r["code"] for r in self.result.tables["coverage"].values()}
        self.assertIn("NO_ANALYSES", codes)
        self.assertIn("NO_JOB_ANALYSIS", codes)
        self.assertNotIn("J5", self.selected())
        row = self.selected()["J4"]
        self.assertEqual(0, row["gross_words"])  # actual returned zero
        self.assertIsNone(row["discounted_words"])
        self.assertIsNone(row["word_savings"])
        self.assertIsNone(row["word_savings_percent"])

    def test_latest_outdated_or_running_is_not_replaced_with_older_counts(self):
        for change in ({"outdated": True}, {"importStatus": {"status": "RUNNING"}}, {"outdated": None}):
            self.api = PhraseFixture()
            self.api.analyses["A2"]["body"].update(change)
            self.run_fixture()
            self.assertNotIn("J1", self.selected())
            self.assertNotIn("J3", self.selected())
            self.assertIn("J2", self.selected())

    def test_latest_inaccessible_analysis_blocks_older_candidate(self):
        self.api.analyses["A2"]["body"]["settings"] = {}  # reference cannot disclose count basis
        self.api.overrides["/api2/v3/analyses/A2"] = (403, {"error": "fixture-secret"})
        self.run_fixture()
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertNotIn("J1", self.selected())
        self.assertEqual("inaccessible", self.result.tables["analyses"]["A2"]["retrieval_status"])

    def test_job_discovery_finds_analysis_missing_from_project_list(self):
        refs = [r for r in self.api.refs() if r["uid"] != "A2"]
        from tests.fakes import page
        self.api.overrides["/api2/v3/projects/P1/analyses"] = lambda q: response(200, page(refs, q))
        self.run_fixture()
        self.assertEqual("A2", self.selected()["J1"]["analysis_uid"])

    def test_unknown_dates_and_count_basis_are_not_guessed(self):
        self.api.analyses["A2"]["body"]["dateCreated"] = None
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertFalse(self.result.manifest()["core_complete"])
        self.api = PhraseFixture()
        self.api.analyses["A2"]["body"]["settings"] = {}
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertIsNone(next(r["total_source_words"] for r in self.result.tables["job_analyses"].values() if r["analysis_uid"] == "A2"))

    def test_language_estimates_and_unavailable_counts_are_not_selected(self):
        for key, value in (("estimate", True), ("available", False)):
            self.api = PhraseFixture()
            self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["data"][key] = value
            self.run_fixture()
            self.assertNotIn("J1", self.selected())
            self.assertIn("J3", self.selected())

    def test_unavailable_net_counts_are_null_and_do_not_destroy_raw(self):
        self.api.analyses["A2"]["members"]["2"][0]["discountedData"]["available"] = False
        self.run_fixture()
        self.assertEqual(100, self.selected()["J1"]["gross_words"])
        self.assertIsNone(self.selected()["J1"]["discounted_words"])
        self.assertIsNone(self.selected()["J1"]["word_savings"])

    def test_dates_filter_analysis_creation_not_project_or_job_creation(self):
        self.run_fixture(start="2026-02-02T00:00:00Z", end="2026-03-02T00:00:00Z")
        self.assertEqual({"J1", "J3"}, set(self.selected()))
        self.assertFalse(self.result.tables["analyses"]["A1"]["in_period"])
        self.assertFalse(self.result.tables["analyses"]["A3"]["in_period"])

    def test_exact_project_and_client_filters(self):
        for options in ({"project_uids": ("P1",)}, {"project_names": ("Product German",)},
                        {"client_ids": (7,)}, {"client_names": ("Client Seven",)}):
            self.run_fixture(**options)
            self.assertEqual({"P1"}, set(self.result.tables["projects"]))
        self.run_fixture(client_ids=(8,))
        self.assertEqual({"P2"}, set(self.result.tables["projects"]))
        self.assertEqual({}, self.selected())

    def test_hidden_client_does_not_bypass_filter(self):
        self.api.projects["P1"]["client"] = None
        self.run_fixture(project_uids=("P1",), client_names=("Client Seven",))
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertEqual({}, self.selected())

    def test_all_members_beyond_embedded_100_are_retrieved(self):
        self.api.jobs = {"BIG" + str(i): self.api.job("BIG" + str(i), "de", 1) for i in range(103)}
        words = {j: 10 for j in self.api.jobs}
        self.api.analyses = {"B1": self.api.analysis("B1", "2026-02-01T00:00:00Z", [("9", "de", words)])}
        self.run_fixture(project_uids=("P1",), download_json=False)
        self.assertTrue(self.result.manifest()["core_complete"])
        self.assertEqual(103, len(self.selected()))
        self.assertEqual(103, len(self.result.tables["job_analyses"]))
        self.assertEqual(3, sum(p == "/api2/v1/analyses/B1/analyseLanguageParts/9/jobs" for p, _ in self.session.calls))

    def test_scheme_and_download_denial_preserve_raw_and_returned_net(self):
        self.api.overrides["/api2/v2/netRateSchemes/S1"] = (403, {})
        self.api.overrides["/api2/v1/analyses/A2/download"] = (403, {})
        self.run_fixture()
        self.assertTrue(self.result.manifest()["core_complete"])
        self.assertFalse(self.result.manifest()["metadata_complete"])
        self.assertFalse(self.result.manifest()["optional_complete"])
        self.assertEqual(50, self.selected()["J1"]["word_savings"])
        self.assertFalse(self.result.tables["net_rate_schemes"]["S1"]["metadata_available"])

    def test_owner_filter_cannot_be_bypassed_by_job_audit(self):
        refs = [r for r in self.api.refs() if r["uid"] != "A2"]
        from tests.fakes import page
        self.api.overrides["/api2/v3/projects/P1/analyses"] = lambda q: response(200, page(refs, q))
        self.run_fixture(only_owner_org=True)
        self.assertNotIn("A2", self.result.tables["analyses"])
        self.assertEqual("A1", self.selected()["J1"]["analysis_uid"])

    def test_fractional_counts_and_negative_savings_not_clamped(self):
        member = self.api.analyses["A2"]["members"]["2"][0]
        member["data"]["all"]["words"] = 1.1
        member["discountedData"]["all"]["words"] = 1.2
        self.run_fixture()
        self.assertEqual(Decimal("-.1"), self.selected()["J1"]["word_savings"])
        self.assertIn("NEGATIVE_SAVINGS", {r["code"] for r in self.result.tables["coverage"].values()})

    def test_returned_update_date_is_preserved_without_substitution(self):
        self.api.analyses["A2"]["body"]["dateUpdated"] = "2026-02-03T00:00:00Z"
        self.run_fixture()
        self.assertEqual("dateUpdated", self.result.tables["analyses"]["A2"]["analysis_updated_at_field"])
        self.assertEqual("2026-02-03T00:00:00Z", self.result.tables["analyses"]["A2"]["analysis_updated_at"])

    def test_target_counts_are_distinguished_and_opt_in(self):
        self.api.analyses["A2"]["body"]["settings"]["countSourceUnits"] = False
        self.run_fixture(count_basis="target")
        self.assertEqual({"J1", "J3"}, set(self.selected()))
        self.assertIsNone(self.selected()["J1"]["total_source_words"])
        self.assertEqual("target", self.selected()["J1"]["count_basis"])

    def test_schema_failure_and_archive_denial_mark_partial(self):
        self.api.overrides["/api2/v1/projects/P1"] = (404, {})
        self.api.overrides["/api2/v2/projects/P1/jobs"] = (404, {})
        self.run_fixture()
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertFalse(self.result.manifest()["metadata_complete"])
        self.assertEqual(4, len(self.result.tables["analyses"]))

    def test_global_auth_failure_stops_discovery(self):
        self.api.overrides["/api2/v1/auth/whoAmI"] = (401, {})
        self.run_fixture()
        self.assertEqual(1, len(self.session.calls))
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertEqual({}, self.selected())

    def test_no_write_requests_and_credential_leakage(self):
        self.api.analyses["A2"]["body"]["name"] = "fixture-secret"
        self.run_fixture()
        self.assertTrue(all(c[1]["allow_redirects"] is False for c in self.session.calls))
        text = dumps([list(t.values()) for t in self.result.tables.values()] + [list(self.client.snapshots.values())])
        self.assertNotIn("fixture-secret", text)


    def test_malformed_language_counts_do_not_abort_other_language_results(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["data"] = ["bad schema"]
        self.run_fixture()
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertNotIn("J1", self.selected())
        self.assertIn("J3", self.selected())

    def test_malformed_job_counts_do_not_abort_other_jobs(self):
        self.api.analyses["A1"]["members"]["1"][0]["data"]["all"] = "bad schema"
        self.run_fixture()
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertIn("J2", self.selected())
        self.assertIn("J1", self.selected())  # valid newer A2

    def test_missing_net_availability_does_not_manufacture_savings(self):
        del self.api.analyses["A2"]["members"]["2"][0]["discountedData"]["available"]
        self.run_fixture()
        self.assertEqual(100, self.selected()["J1"]["gross_words"])
        self.assertIsNone(self.selected()["J1"]["discounted_words"])
        self.assertIsNone(self.selected()["J1"]["word_savings"])

    def test_conflicting_language_and_job_totals_mark_partial(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["data"]["all"]["words"] = 101
        self.run_fixture()
        self.assertFalse(self.result.manifest()["core_complete"])
        self.assertIn("TOTAL_MISMATCH", {r["code"] for r in self.result.tables["coverage"].values()})
        self.assertEqual(100, self.selected()["J1"]["gross_words"])

    def test_unknown_analysis_type_cannot_cause_unsafe_fallback(self):
        del self.api.analyses["A2"]["body"]["type"]
        self.api.overrides["/api2/v3/analyses/A2"] = (403, {})
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertFalse(self.result.manifest()["core_complete"])

    def test_timestamp_ties_have_deterministic_uid_order(self):
        self.api.analyses["A2"]["body"]["dateCreated"] = self.api.analyses["A1"]["body"]["dateCreated"]
        self.run_fixture()
        self.assertEqual("A2", self.selected()["J1"]["analysis_uid"])

    def test_required_membership_failure_is_not_replaced_by_embedded_counts(self):
        self.api.overrides["/api2/v1/analyses/A2/analyseLanguageParts/2/jobs"] = (403, {})
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertIn("J3", self.selected())
        self.assertFalse(self.result.manifest()["core_complete"])

    def test_native_json_download_can_be_disabled(self):
        self.run_fixture(download_json=False)
        self.assertFalse(any(path.endswith("/download") for path, _ in self.session.calls))
        self.assertEqual(4, len(self.selected()))

    def test_parent_and_split_parts_with_overlapping_ranges_are_excluded(self):
        self.api.jobs["J2"]["workflowLevel"] = 1
        self.api.jobs["J2"]["workflowStep"]["workflowLevel"] = 1
        self.api.jobs["J2"]["beginIndex"] = 5
        self.api.jobs["J2"]["endIndex"] = 15
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertNotIn("J2", self.selected())
        self.assertIn("J3", self.selected())
        self.assertIn("OVERLAPPING_JOB_PARTS", {r["code"] for r in self.result.tables["coverage"].values()})

    def test_adjacent_nonoverlapping_parts_are_retained(self):
        self.api.jobs["J2"]["workflowLevel"] = 1
        self.api.jobs["J2"]["workflowStep"]["workflowLevel"] = 1
        self.api.jobs["J2"]["beginIndex"] = 11
        self.api.jobs["J2"]["endIndex"] = 20
        self.run_fixture()
        self.assertIn("J1", self.selected())
        self.assertIn("J2", self.selected())

    def test_inconsistent_job_and_language_metadata_blocks_selection(self):
        self.api.jobs["J1"]["targetLang"] = "es"
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertIn("J3", self.selected())
        self.assertFalse(self.result.manifest()["core_complete"])

    def test_missing_language_metadata_is_not_guessed(self):
        self.api.analyses["A2"]["body"]["analyseLanguageParts"][0]["targetLang"] = None
        self.run_fixture()
        self.assertNotIn("J1", self.selected())
        self.assertFalse(self.result.manifest()["core_complete"])


if __name__ == "__main__":
    unittest.main()
