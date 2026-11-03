from copy import deepcopy
from decimal import Decimal
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from leverage.client import TmsClient
from leverage.config import ConfigurationError, Settings
from leverage.export import export
from leverage.extractor import Extraction
from leverage.numeric import difference, dumps, percentage, total
from leverage.reporting import DETAIL_COLUMNS, Reports
from tests.fakes import FakeSession, PhraseFixture


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.api = PhraseFixture()

    def run_fixture(self, **options):
        settings = {"authorization": "ApiToken fixture-secret", "start": "2026-01-01", "end": "2027-01-01", "download_json": False}
        settings.update(options)
        self.extraction = Extraction(TmsClient(Settings(**settings), FakeSession(self.api), Mock(), lambda: 0)).run()
        self.reports = Reports(self.extraction)
        return self.reports

    def project(self, project="P1", target="de", level=1, kind="PreAnalyse"):
        return next(r for r in self.reports.projects if r["project_uid"] == project and r["target_language"] == target and
                    r["workflow_level"] == level and r["analysis_type"] == kind)

    def add_project(self, analysed=True, client=None, words=300):
        self.api.projects["P3"] = {"uid": "P3", "name": "Same client second project",
                                   "client": {"id": "7", "name": "Client Seven"} if client is None else client}
        self.api.jobs["J6"] = self.api.job("J6", "de", 1, "P3")
        if analysed:
            self.api.analyses["A6"] = self.api.analysis("A6", "2026-05-01T00:00:00Z", [("6", "de", {"J6": words})])
            self.api.analyses["A6"]["body"]["project"]["uid"] = "P3"

    def test_detail_pivots_units_to_unique_actual_category_rows(self):
        reports = self.run_fixture()
        self.assertEqual(48, len(reports.details))
        identities = {(r["analysis_uid"], r["job_uid"], r["source_language"], r["target_language"], r["origin_key"], r["match_category"]) for r in reports.details}
        self.assertEqual(len(reports.details), len(identities))
        self.assertFalse(any(r["origin_key"] == "all" for r in reports.details))
        row = next(r for r in reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1" and
                   r["match_origin"] == "TM" and r["match_category"] == "match100")
        self.assertEqual((20, 10, 10, 100, 2), (row["gross_words"], row["discounted_words"], row["saved_words"], row["gross_characters"], row["gross_segments"]))
        self.assertEqual("Step 1", row["workflow_step"])
        self.assertTrue(row["selected_for_summary"])
        self.assertEqual("complete", row["data_status"])
        self.assertEqual("Standard", row["net_rate_scheme"])

    def test_summary_uses_selected_default_jobs_once_at_workflow_one(self):
        self.run_fixture()
        row = self.project()
        self.assertEqual((100, 50, 50, 50), (row["total_gross_words"], row["discounted_words"], row["saved_words"], row["leverage_percentage"]))
        self.assertEqual((50, 10, 10, 10, 10), (row["tm_words"], row["mt_words"], row["non_translatable_words"], row["internal_fuzzy_words"], row["repetition_words"]))
        self.assertEqual((1, 0), (row["number_of_jobs_covered"], row["number_of_jobs_missing_valid_analyses"]))
        self.assertEqual(4, len(self.reports.projects))
        self.assertTrue(any(r["analysis_uid"] == "A1" and r["job_uid"] == "J1" and not r["selected_for_summary"] for r in self.reports.details))

    def test_workflows_and_postediting_are_separate_scopes(self):
        self.run_fixture(report_workflow_levels=(1, 2), summary_types=("PreAnalyse", "PostAnalyse"))
        self.assertEqual(100, self.project()["total_gross_words"])
        self.assertEqual(120, self.project(level=2)["total_gross_words"])
        self.assertEqual(120, self.project(level=2, kind="PostAnalyse")["total_gross_words"])
        scope_ids = {self.project()["scope_id"], self.project(level=2)["scope_id"], self.project(level=2, kind="PostAnalyse")["scope_id"]}
        self.assertEqual(3, len(scope_ids))
        identities = {(r["project_uid"], r["source_language"], r["target_language"], r["scope_id"]) for r in self.reports.projects}
        self.assertEqual(len(self.reports.projects), len(identities))

    def test_actual_101_and_100_matches_remain_distinct(self):
        analysis = self.api.analyses["A2"]
        for block in (analysis["body"]["analyseLanguageParts"][0]["data"], analysis["members"]["2"][0]["data"]):
            block["transMemoryMatches"]["match101"]["words"] = 5
            block["transMemoryMatches"]["match0"]["words"] -= 5
        self.run_fixture()
        tm = {r["match_category"]: r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1" and r["match_origin"] == "TM"}
        self.assertEqual(5, tm["match101"]["gross_words"])
        self.assertEqual(20, tm["match100"]["gross_words"])
        self.assertEqual(5, self.project()["gross_category_totals"]["transMemoryMatches"]["match101"])

    def test_missing_band_is_not_filled_with_zero(self):
        for payload in (self.api.analyses["A2"]["body"]["analyseLanguageParts"][0], self.api.analyses["A2"]["members"]["2"][0]):
            for block in (payload["data"], payload["discountedData"]):
                del block["transMemoryMatches"]["match101"]
        self.run_fixture()
        rows = [r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1"]
        self.assertFalse(any(r["match_category"] == "match101" for r in rows))

    def test_raw_missing_band_and_returned_discounted_zero_are_separate(self):
        for payload in (self.api.analyses["A2"]["body"]["analyseLanguageParts"][0], self.api.analyses["A2"]["members"]["2"][0]):
            del payload["data"]["transMemoryMatches"]["match101"]
        self.run_fixture()
        row = next(r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1" and r["match_category"] == "match101")
        self.assertIsNone(row["gross_words"])
        self.assertEqual(0, row["discounted_words"])
        self.assertIsNone(row["saved_words"])
        self.assertFalse(row["raw_category_present"])
        self.assertEqual("raw_category_not_returned", row["data_status"])

    def test_unknown_origins_and_v1_nt_bands_are_retained(self):
        self.run_fixture()
        self.assertTrue(any(r["match_origin"] == "futureMatchCategory" and r["match_category"] == "match97" for r in self.reports.details))
        self.assertTrue(any(r["match_origin"] == "NT" and r["match_category"] == "match99" for r in self.reports.details))
        self.assertEqual(10, self.project()["gross_category_totals"]["futureMatchCategory"]["match97"])
        self.assertTrue(self.reports.validation.summary()["valid"])

    def test_raw_category_mismatch_excludes_invalid_job_without_adjustment(self):
        member = self.api.analyses["A2"]["members"]["2"][0]
        member["data"]["transMemoryMatches"]["match100"]["words"] = 21
        self.run_fixture()
        self.assertIsNone(self.project()["total_gross_words"])
        self.assertEqual(1, self.project()["number_of_jobs_missing_valid_analyses"])
        row = next(r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1" and r["match_category"] == "match100" and r["match_origin"] == "TM")
        self.assertEqual(21, row["gross_words"])
        self.assertIsNone(row["saved_words"])
        self.assertFalse(row["selected_for_summary"])
        self.assertEqual("raw_validation_failed", row["data_status"])
        check = next(c for c in self.reports.validation.checks if c.get("job_uid") == "J1" and c.get("analysis_uid") == "A2" and c.get("unit") == "words" and c.get("count_kind") == "raw")
        self.assertEqual((100, 101, 1, "failed"), (check["phrase_total"], check["category_total"], check["difference"], check["status"]))

    def test_character_or_segment_mismatch_is_validated_too(self):
        for unit in ("characters", "segments"):
            self.api = PhraseFixture()
            self.api.analyses["A2"]["members"]["2"][0]["data"]["repetitions"][unit] += 1
            self.run_fixture()
            self.assertIsNone(self.project()["total_gross_words"])
            self.assertTrue(any(c.get("unit") == unit and c["status"] == "failed" for c in self.reports.validation.checks))

    def test_valid_job_categories_with_inconsistent_language_characters_are_excluded(self):
        member = self.api.analyses["A2"]["members"]["2"][0]
        member["data"]["all"]["characters"] += 1
        member["data"]["repetitions"]["characters"] += 1
        self.run_fixture()
        self.assertIsNone(self.project()["total_gross_words"])
        self.assertTrue(any(c["check"] == "job_membership_total" and c.get("unit") == "characters" and c["status"] == "failed"
                            for c in self.reports.validation.checks))

    def test_duplicate_inventory_record_does_not_duplicate_details_or_summary(self):
        self.run_fixture()
        original = self.reports
        key, row = next(iter(self.extraction.tables["job_analyses"].items()))
        self.extraction.tables["job_analyses"][("duplicate", *key)] = deepcopy(row)
        reports = Reports(self.extraction)
        self.assertEqual(len(original.details), len(reports.details))
        self.assertEqual(original.projects, reports.projects)
        self.assertTrue(any(c["check"] == "duplicate_job_record" for c in reports.validation.checks))

    def test_invalid_net_does_not_discard_valid_raw_or_create_savings(self):
        self.api.analyses["A2"]["members"]["2"][0]["discountedData"]["repetitions"]["words"] += 1
        self.run_fixture()
        row = self.project()
        self.assertEqual(100, row["total_gross_words"])
        self.assertIsNone(row["discounted_words"])
        self.assertIsNone(row["saved_words"])
        self.assertIsNone(row["leverage_percentage"])
        self.assertEqual("partial_discounted", row["data_status"])

    def test_missing_net_and_returned_zero_are_different(self):
        self.run_fixture()
        row = self.project(target="ja")
        self.assertEqual(0, row["total_gross_words"])
        self.assertIsNone(row["discounted_words"])
        self.assertIsNone(row["saved_words"])
        self.assertIsNone(row["leverage_percentage"])
        missing = self.project(project="P2")
        self.assertIsNone(missing["total_gross_words"])
        self.assertIsNone(missing["known_gross_words"])
        self.assertEqual((0, 1), (missing["number_of_jobs_covered"], missing["number_of_jobs_missing_valid_analyses"]))

    def test_unavailable_values_are_not_genuine_zeros(self):
        member = self.api.analyses["A2"]["members"]["2"][0]
        member["data"]["available"] = False
        self.run_fixture()
        rows = [r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1"]
        self.assertTrue(all(r["gross_words"] is None for r in rows))
        self.assertTrue(any(r["raw_metrics"].get("words") == 20 for r in rows))
        self.assertTrue(all(r["data_status"] == "raw_unavailable" for r in rows))

    def test_client_aggregation_uses_project_rows_and_resolves_id_uid_alias(self):
        self.add_project()
        self.run_fixture()
        client = next(r for r in self.reports.clients if r["client_identity"] == "uid:C7" and r["target_language"] == "de")
        self.assertEqual((400, 200, 200, 50), (client["total_gross_words"], client["discounted_words"], client["saved_words"], client["leverage_percentage"]))
        self.assertEqual((2, 2), (client["number_of_projects"], client["number_of_jobs_covered"]))
        self.assertEqual("C7", client["client_uid"])

    def test_partial_client_totals_are_null_with_explicit_known_subset(self):
        self.add_project(analysed=False)
        self.run_fixture()
        row = next(r for r in self.reports.clients if r["client_identity"] == "uid:C7" and r["target_language"] == "de")
        self.assertIsNone(row["total_gross_words"])
        self.assertIsNone(row["discounted_words"])
        self.assertIsNone(row["saved_words"])
        self.assertIsNone(row["leverage_percentage"])
        self.assertEqual((100, 50, 50), (row["known_gross_words"], row["known_discounted_words"], row["known_saved_words"]))
        self.assertEqual((100, 50), (row["known_leverage_denominator_words"], row["known_leverage_percentage"]))
        self.assertEqual(1, row["number_of_jobs_missing_valid_analyses"])

    def test_same_named_clients_with_distinct_ids_are_not_combined(self):
        self.add_project(client={"id": "99", "uid": "C99", "name": "Client Seven"})
        self.run_fixture()
        rows = [r for r in self.reports.clients if r["client_name"] == "Client Seven" and r["target_language"] == "de"]
        self.assertEqual(2, len(rows))

    def test_unresolved_clients_are_kept_per_project(self):
        self.add_project(client={})
        self.api.projects["P2"]["client"] = None
        self.run_fixture()
        unknown = [r for r in self.reports.clients if not r["client_identity_resolved"] and r["target_language"] == "de"]
        self.assertEqual(2, len(unknown))
        self.assertTrue(all(r["number_of_projects"] == 1 for r in unknown))

    def test_gross_denominator_is_weighted_not_average_job_percent(self):
        self.add_project(words=300)
        a6 = self.api.analyses["A6"]
        for block in (a6["body"]["analyseLanguageParts"][0]["discountedData"], a6["members"]["6"][0]["discountedData"]):
            for counts in [block["all"], block["repetitions"], *block["transMemoryMatches"].values(), *block["machineTranslationMatches"].values(),
                           *block["internalFuzzyMatches"].values(), *block["nonTranslatablesMatches"].values(), *block["futureMatchCategory"].values()]:
                for unit in ("words", "characters", "segments"):
                    counts[unit] *= 2
        self.run_fixture()
        row = next(r for r in self.reports.clients if r["client_identity"] == "uid:C7" and r["target_language"] == "de")
        self.assertEqual((400, 350, 50, Decimal("12.5")), (row["total_gross_words"], row["discounted_words"], row["saved_words"], row["leverage_percentage"]))
        self.assertEqual(400, row["leverage_denominator_words"])

    def test_validation_tolerance_does_not_round_or_adjust_values(self):
        value = Decimal("20.0000001")
        self.api.analyses["A2"]["members"]["2"][0]["data"]["transMemoryMatches"]["match100"]["words"] = value
        self.run_fixture()
        self.assertTrue(self.reports.validation.summary()["valid"])
        row = next(r for r in self.reports.details if r["analysis_uid"] == "A2" and r["job_uid"] == "J1" and r["match_origin"] == "TM" and r["match_category"] == "match100")
        self.assertEqual(value, row["gross_words"])
        self.run_fixture(category_tolerance=0)
        self.assertFalse(self.reports.validation.summary()["valid"])

    def test_language_part_ambiguity_is_excluded_not_merged_or_prorated(self):
        analysis = self.api.analyses["A2"]
        duplicate = deepcopy(analysis["body"]["analyseLanguageParts"][0])
        duplicate["id"] = "22"
        analysis["body"]["analyseLanguageParts"].append(duplicate)
        analysis["members"]["22"] = deepcopy(analysis["members"]["2"])
        self.run_fixture()
        self.assertFalse(any(r["analysis_uid"] == "A2" and r["job_uid"] == "J1" for r in self.reports.details))
        self.assertTrue(any(c["check"] == "overlapping_language_parts" for c in self.reports.validation.checks))
        self.assertIsNone(self.project()["total_gross_words"])
        structured = self.reports.structured()
        self.assertEqual(3, len(next(a for a in structured["analysis_data"] if a["metadata"]["analysis_uid"] == "A2")["language_parts"]))

    def test_json_preserves_original_raw_and_discounted_categories(self):
        self.run_fixture()
        structured = self.reports.structured()
        analysis = next(a for a in structured["analysis_data"] if a["metadata"]["analysis_uid"] == "A2")
        language = next(p for p in analysis["language_parts"] if p["metadata"]["id"] == "2")
        self.assertIn("match95", language["data"]["nonTranslatablesMatches"])
        self.assertIn("match99", language["jobs"][0]["data"]["nonTranslatablesMatches"])
        self.assertEqual(100, language["jobs"][0]["data"]["all"]["words"])
        self.assertEqual(50, language["jobs"][0]["discountedData"]["all"]["words"])

    def test_high_precision_csv_json_and_savings_are_not_rounded(self):
        gross = Decimal("12345678901234567890.1234567890123456789")
        net = Decimal("1.0000000000000000001")
        raw_block = {"available": True, "all": {"words": gross, "characters": gross, "segments": 1},
                     "transMemoryMatches": {"match100": {"words": gross, "characters": gross, "segments": 1}}}
        net_block = {"available": True, "all": {"words": net, "characters": net, "segments": 1},
                     "transMemoryMatches": {"match100": {"words": net, "characters": net, "segments": 1}}}
        a2 = self.api.analyses["A2"]
        for payload in (a2["body"]["analyseLanguageParts"][0], a2["members"]["2"][0]):
            payload.update(data=deepcopy(raw_block), discountedData=deepcopy(net_block))
        self.run_fixture()
        with tempfile.TemporaryDirectory() as directory:
            manifest = export(self.extraction, directory)
            csv_text = (Path(directory) / manifest["report_files"]["detailed"]).read_text(encoding="utf-8")
            self.assertIn(str(gross), csv_text)
            self.assertIn("12345678901234567889.1234567890123456788", csv_text)
            data = json.loads((Path(directory) / manifest["report_files"]["structured_json"]).read_text(encoding="utf-8"), parse_float=Decimal)
            a = next(a for a in data["analysis_data"] if a["metadata"]["analysis_uid"] == "A2")
            self.assertEqual(gross, a["language_parts"][0]["data"]["all"]["words"])

    def test_named_outputs_have_required_headers_even_with_no_data(self):
        self.api.projects = {}
        self.api.jobs = {}
        self.api.analyses = {}
        self.run_fixture()
        with tempfile.TemporaryDirectory() as directory:
            manifest = export(self.extraction, directory)
            for key in ("detailed", "project_summary", "client_summary"):
                path = Path(directory) / manifest["report_files"][key]
                rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
                self.assertEqual(1, len(rows))
                self.assertIn("project_uid" if key != "client_summary" else "client_name", rows[0])
                if key == "detailed":
                    self.assertTrue(set(DETAIL_COLUMNS).issubset(rows[0]))
            self.assertFalse(manifest["client_reporting_complete"])

    def test_named_reports_are_idempotent_and_checksummed(self):
        self.run_fixture()
        with tempfile.TemporaryDirectory() as directory:
            first = export(self.extraction, directory)
            details = (Path(directory) / first["report_files"]["detailed"]).read_bytes()
            second = export(self.extraction, directory)
            self.assertEqual(first["report_files"], second["report_files"])
            self.assertEqual(details, (Path(directory) / second["report_files"]["detailed"]).read_bytes())
            self.assertEqual(hashlib.sha256(details).hexdigest(), second["report_sha256"]["detailed"])
            self.assertEqual(5, len([p for p in Path(directory).iterdir() if "Phrase_Leverage_Stats" in p.name]))

    def test_invalid_categories_mark_manifest_partial(self):
        self.api.analyses["A2"]["members"]["2"][0]["data"]["repetitions"]["words"] += 1
        self.run_fixture()
        with tempfile.TemporaryDirectory() as directory:
            manifest = export(self.extraction, directory)
            self.assertFalse(manifest["core_complete"])
            self.assertEqual("partial", manifest["status"])
            self.assertGreater(manifest["report_validation"]["check_counts"]["failed"], 0)

    def test_mixed_settings_are_flagged_without_hiding_original_metadata(self):
        self.add_project()
        self.api.analyses["A6"]["body"]["settings"]["includeNumbers"] = True
        self.run_fixture()
        row = next(r for r in self.reports.clients if r["client_identity"] == "uid:C7" and r["target_language"] == "de")
        self.assertTrue(row["mixed_analysis_settings"])
        self.assertEqual(2, len(row["analysis_settings_fingerprints"]))


class ExactNumericTests(unittest.TestCase):
    def test_large_and_tiny_numbers_sum_and_subtract_exactly(self):
        value = Decimal("1e100")
        tiny = Decimal("1e-50")
        result = total((value, tiny))
        self.assertEqual(tiny, difference(result, value))
        self.assertEqual("0.1", str(difference(Decimal("0.3"), Decimal("0.2"))))
        self.assertIsNone(total([None]))
        self.assertIsNone(percentage(0, 0))

    def test_json_decimal_numbers_are_not_strings(self):
        value = Decimal("0.123456789012345678901234567890123456789")
        text = dumps({"value": value})
        self.assertEqual(value, json.loads(text, parse_float=Decimal)["value"])
        self.assertNotIn('"0.123', text)

    def test_report_settings_validate_scope_and_tolerance(self):
        for options in ({"report_workflow_levels": ()}, {"report_workflow_levels": (0,)}, {"category_tolerance": "NaN"}, {"category_tolerance": -1}):
            with self.assertRaises(ConfigurationError):
                Settings(authorization="ApiToken fixture-secret", **options)


if __name__ == "__main__":
    unittest.main()
