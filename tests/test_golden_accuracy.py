"""End-to-end pricing checks against independent, literal expected results."""

from collections import defaultdict
import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from leverage.client import TmsClient
from leverage.config import Settings
from leverage.export import export
from leverage.extractor import Extraction
from leverage.reporting import Reports
from tests.fakes import FakeSession, PhraseFixture


GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "leverage_golden.json").read_text(encoding="utf-8"))


def counts(factor, control, discounted=False, language_part=False):
    def units(words):
        return {"words": words, "characters": words * 5, "segments": words / 10}
    result = {"available": True, "all": units(Decimal(control))}
    if language_part:
        result["estimate"] = False
    for origin, band, gross, net in GOLDEN["categories"]:
        if origin == "nonTranslatablesMatches" and language_part:
            band = "match95"  # v3 uses different NT bands; no remapping in application.
        value = units(Decimal(net if discounted else gross) * factor)
        if band is None:
            result[origin] = value
        else:
            result.setdefault(origin, {})[band] = value
    return result


def fixture():
    api = PhraseFixture()
    api.jobs, api.analyses = {}, {}
    for p in ("P1", "P2"):
        api.projects[p] = {"uid": p, "name": "Golden " + p, "sourceLang": "en", "targetLangs": ["de", "fr"],
                           "client": {"uid": "C7", "id": "7", "name": "Golden client"}}
        specs = [j for j in GOLDEN["jobs"] if j["project"] == p]
        grouped = {target: {j["uid"]: int(Decimal(j["gross"])) for j in specs if j["target"] == target} for target in ("de", "fr")}
        analysis = api.analysis("A" + p, "2026-06-01T00:00:00Z", [(str(i), target, grouped[target]) for i, target in enumerate(("de", "fr"), 1)])
        analysis["body"]["project"]["uid"] = p
        for j in specs:
            api.jobs[j["uid"]] = api.job(j["uid"], j["target"], 1, p)
        for part in analysis["body"]["analyseLanguageParts"]:
            expected = next(e for e in GOLDEN["projects"] if e["project"] == p and e["target"] == part["targetLang"])
            multiplier = sum(Decimal(j["factor"]) for j in specs if j["target"] == part["targetLang"])
            for field, is_net, control in (("data", False, "gross"), ("discountedData", True, "net")):
                part[field] = counts(multiplier, expected[control], is_net, True)
                for member in analysis["members"][part["id"]]:
                    j = next(j for j in specs if j["uid"] == member["uid"])
                    member[field] = counts(Decimal(j["factor"]), j[control], is_net)
        api.analyses["A" + p] = analysis
    return api


class GoldenAccuracyTests(unittest.TestCase):
    def setUp(self):
        self.extraction = Extraction(TmsClient(
            Settings(authorization="ApiToken synthetic-golden", start="2026-01-01", end="2027-01-01", download_json=False),
            FakeSession(fixture()), Mock(), lambda: 0)).run()
        self.reports = Reports(self.extraction)

    def assert_summary(self, row, expected):
        fields = {"gross": "total_gross_words", "net": "discounted_words", "saved": "saved_words", "tm": "tm_words",
                  "mt": "mt_words", "nt": "non_translatable_words", "internal": "internal_fuzzy_words", "repetition": "repetition_words"}
        for key, field in fields.items():
            self.assertEqual(Decimal(expected[key]), Decimal(row[field]), field)
        self.assertEqual(Decimal("66.5"), Decimal(row["leverage_percentage"]))
        self.assertEqual(expected["jobs"], int(row["number_of_jobs_covered"]))
        self.assertEqual(0, int(row["number_of_jobs_missing_valid_analyses"]))
        self.assertEqual("complete", row["data_status"])

    def test_two_projects_two_languages_multiple_jobs_against_literal_expectations(self):
        self.assertEqual(4, len(self.reports.projects))
        self.assertEqual(2, len(self.reports.clients))
        for expected in GOLDEN["projects"]:
            row = next(r for r in self.reports.projects if r["project_uid"] == expected["project"] and r["target_language"] == expected["target"])
            self.assert_summary(row, expected)
        for expected in GOLDEN["clients"]:
            row = next(r for r in self.reports.clients if r["target_language"] == expected["target"])
            self.assert_summary(row, expected)
            self.assertEqual(2, row["number_of_projects"])
        self.assertTrue(self.reports.validation.summary()["valid"])

    def test_detailed_categories_reconcile_to_each_literal_job_total(self):
        self.assertEqual(40, len(self.reports.details))
        groups = defaultdict(list)
        for row in self.reports.details:
            self.assertTrue(row["selected_for_summary"])
            groups[row["job_uid"]].append(row)
        for j in GOLDEN["jobs"]:
            rows = groups[j["uid"]]
            self.assertEqual(Decimal(j["gross"]), sum(r["gross_words"] for r in rows))
            self.assertEqual(Decimal(j["net"]), sum(r["discounted_words"] for r in rows))
            self.assertEqual(Decimal(j["gross"]) - Decimal(j["net"]), sum(r["saved_words"] for r in rows))
        exact = {r["match_category"]: r for r in groups["D1"] if r["match_origin"] == "TM"}
        self.assertEqual((100, 200, 0, 40), (exact["match101"]["gross_words"], exact["match100"]["gross_words"],
                                           exact["match101"]["discounted_words"], exact["match100"]["discounted_words"]))

    def test_csv_and_json_agree_with_independent_expected_values(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = export(self.extraction, directory)
            self.assertTrue(manifest["core_complete"])
            self.assertTrue(manifest["client_reporting_complete"])
            for kind, expectations in (("project_summary", GOLDEN["projects"]), ("client_summary", GOLDEN["clients"])):
                with (Path(directory) / manifest["report_files"][kind]).open(encoding="utf-8", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                for expected in expectations:
                    row = next(r for r in rows if r["target_language"] == expected["target"] and
                               (kind == "client_summary" or r["project_uid"] == expected["project"]))
                    self.assert_summary(row, expected)
            payload = json.loads((Path(directory) / manifest["report_files"]["structured_json"]).read_text(encoding="utf-8"), parse_float=Decimal)
            self.assertEqual(self.reports.projects, payload["project_summary"])
            self.assertEqual(2, len(payload["analysis_data"]))
            self.assertEqual(5, len(payload["job_inventory"]))


if __name__ == "__main__":
    unittest.main()
