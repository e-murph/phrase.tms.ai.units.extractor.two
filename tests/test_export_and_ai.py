import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import openpyxl

from leverage.client import TmsClient
from leverage.config import Settings
from leverage.export import export
from leverage.extractor import Extraction
from leverage_main import main
from tests.fakes import FakeSession, PhraseFixture


ROOT = Path(__file__).resolve().parents[1]


class ExportAndCliTests(unittest.TestCase):
    def test_repeated_execution_replaces_snapshots_with_stable_ids(self):
        api = PhraseFixture()
        config = Settings(authorization="ApiToken fixture-secret", start="2026-01-01", end="2027-01-01")
        with tempfile.TemporaryDirectory() as directory:
            before = None
            for _ in range(2):
                result = Extraction(TmsClient(config, FakeSession(api), Mock(), lambda: 0)).run()
                manifest = export(result, directory)
                current = (Path(directory) / "selected_jobs.jsonl").read_text(encoding="utf-8")
                if before is not None:
                    self.assertEqual(before, current)
                before = current
                self.assertEqual(4, len(current.splitlines()))
                self.assertEqual(4, manifest["record_counts"]["selected_jobs"])
                self.assertTrue(manifest["core_complete"])
                self.assertFalse(manifest["client_reporting_complete"])
                self.assertEqual("partial", manifest["status"])  # Fixture includes jobs without analyses/net data.
            self.assertNotIn("fixture-secret", "".join(p.read_text(encoding="utf-8") for p in Path(directory).glob("*.json*")))
            workbook = openpyxl.load_workbook(Path(directory) / "Leverage_Statistics.xlsx", read_only=True)
            self.assertIn("Category measures", workbook.sheetnames)
            self.assertEqual(5, workbook["Selected jobs"].max_row or len(list(workbook["Selected jobs"].values)))
            workbook.close()

    def test_formula_strings_neutralized_only_in_spreadsheet_formats(self):
        api = PhraseFixture()
        api.projects["P1"]["name"] = "=HYPERLINK(\"untrusted\")"
        result = Extraction(TmsClient(Settings(authorization="ApiToken fixture-secret"), FakeSession(api), Mock(), lambda: 0)).run()
        with tempfile.TemporaryDirectory() as directory:
            export(result, directory)
            projects = [json.loads(line) for line in (Path(directory) / "projects.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(p["project_name"].startswith("=") for p in projects))
            self.assertIn("'=HYPERLINK", (Path(directory) / "projects.csv").read_text(encoding="utf-8"))
            workbook = openpyxl.load_workbook(Path(directory) / "Leverage_Statistics.xlsx", read_only=True)
            self.assertFalse(any(cell.data_type == "f" for row in workbook["Projects"] for cell in row))
            workbook.close()

    def test_missing_rest_credential_fails_without_any_http_or_ai_import(self):
        with patch.dict(os.environ, {}, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP allowed")), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(2, main([]))
        self.assertIn("dashboard URLs", log.getvalue())

    def test_dashboard_secret_selected_as_credential_is_rejected(self):
        value = "https://analytics.cloud.memsource.com/embed/private-JWT"
        with patch.dict(os.environ, {"PHRASE_API_URL": value}, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP allowed")), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(2, main(["--credential-env", "PHRASE_API_URL"]))
        self.assertNotIn(value, log.getvalue())

    def test_email_password_is_not_sent_to_phrase(self):
        with patch.dict(os.environ, {"EMAIL_PASSWORD": "not-a-rest-token"}, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP allowed")), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(2, main(["--credential-env", "EMAIL_PASSWORD"]))
        self.assertNotIn("not-a-rest-token", log.getvalue())

    def test_cli_uses_supplied_existing_token_env_and_saves_partial_failure(self):
        api = PhraseFixture()
        api.overrides["/api2/v1/auth/whoAmI"] = (401, {})
        session = FakeSession(api)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EXISTING_REST_SECRET": "fixture-secret"}, clear=True), \
                patch.dict("sys.modules", {"dotenv": None}), patch("leverage.client.requests.Session", return_value=session), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, main(["--credential-env", "EXISTING_REST_SECRET", "--output", directory]))
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["core_complete"])
            self.assertEqual("ApiToken fixture-secret", session.calls[0][1]["headers"]["Authorization"])
        self.assertTrue(session.closed)


class AiRegressionTests(unittest.TestCase):
    def test_existing_ai_output_contract_with_real_workbook_writer(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        settings = (ROOT / "mapping_settings.json").read_text(encoding="utf-8")
        project = json.loads(settings)["products"][0]["projects"]["de-DE"]
        env = {"PHRASE_API_URL": "https://example.invalid/ai", "PHRASE_REFERER": "https://example.invalid/ref",
               "PHRASE_API_URL_WORDS": "https://example.invalid/words", "PHRASE_REFERER_WORDS": "https://example.invalid/ref"}
        ai = Mock(status_code=200)
        ai.json.return_value = {"data": {"rows": [[project, None, None, 2.5], ["Unmapped es-ES 99", None, None, 1]]}}
        words = Mock(status_code=200)
        words.json.return_value = {"data": {"rows": [[project, None, None, None, "100"], ["Unmapped es-ES 99", None, None, None, "20"]]}}
        old_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path("mapping_settings.json").write_text(settings, encoding="utf-8")
                with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                        patch("requests.get", side_effect=[ai, words]) as get, \
                        patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP allowed")), \
                        contextlib.redirect_stdout(io.StringIO()):
                    namespace = {"__name__": "ai_regression"}
                    exec(compile(source, str(ROOT / "main.py"), "exec"), namespace)
                    namespace["extract_ai_units"]()
                self.assertEqual(2, get.call_count)
                self.assertTrue(all(c.kwargs["params"] == {"date": "thisyear"} for c in get.call_args_list))
                self.assertEqual(5, len(list(Path(directory).glob("*.xlsx"))))
                workbook = openpyxl.load_workbook(next(Path(directory).glob("Master_Phrase_Usage_Stats_*.xlsx")))
                rows = list(workbook["Data"].values)
                self.assertEqual(("Project Name", "Product Name", "Language", "AI Units", "Wordcount"), rows[0])
                self.assertEqual((3.5, 120), rows[-1][3:])
                self.assertTrue(all(c.font.bold for c in workbook["Data"][workbook["Data"].max_row]))
                workbook.close()
            finally:
                os.chdir(old_directory)


if __name__ == "__main__":
    unittest.main()
