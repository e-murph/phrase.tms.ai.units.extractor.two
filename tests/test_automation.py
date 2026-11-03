from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import openpyxl
import requests

import automation
from tests.fakes import response


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)


class PlanTests(unittest.TestCase):
    def test_no_configuration_preserves_scheduled_usage(self):
        plan = automation.create_plan({}, NOW)
        self.assertEqual("usage", plan["mode"])
        self.assertTrue(plan["usage"])
        self.assertFalse(plan["leverage"])
        self.assertEqual("dashboard date=thisyear", plan["usage_period"])

    def test_schedule_variable_and_manual_override_support_all_modes(self):
        for mode in ("usage", "leverage", "both"):
            plan = automation.create_plan({"PHRASE_REPORT_MODE": mode}, NOW)
            self.assertEqual(mode in {"usage", "both"}, plan["usage"])
            self.assertEqual(mode in {"leverage", "both"}, plan["leverage"])
        plan = automation.create_plan({"PHRASE_REPORT_MODE": "both", "INPUT_MODE": "usage"}, NOW)
        self.assertEqual("usage", plan["mode"])

    def test_invalid_mode_fails_without_echoing_untrusted_input(self):
        with self.assertRaises(automation.ConfigurationError) as error:
            automation.create_plan({"INPUT_MODE": "private-token-value"}, NOW)
        self.assertNotIn("private-token-value", str(error.exception))

    def test_default_leverage_window_is_frozen_utc_ytd(self):
        plan = automation.create_plan({"INPUT_MODE": "both"}, NOW)
        self.assertIsNone(plan["leverage_error"])
        config = plan["leverage_settings"]
        self.assertEqual("2026-01-01T00:00:00Z", config["period_start"])
        self.assertEqual("2026-11-03T16:00:00Z", config["period_end"])
        self.assertEqual("PHRASE_TMS_AUTHORIZATION", plan["credential_secret"])

    def test_dates_convert_offsets_and_all_mode_retains_no_lower_boundary(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage", "INPUT_START": "2026-01-01T01:00:00+01:00",
                                       "INPUT_END": "2026-10-01"}, NOW)
        self.assertEqual("2026-01-01T00:00:00Z", plan["leverage_settings"]["period_start"])
        self.assertEqual("2026-10-01T00:00:00Z", plan["leverage_settings"]["period_end"])
        plan = automation.create_plan({"INPUT_MODE": "leverage", "INPUT_PERIOD": "all"}, NOW)
        self.assertIsNone(plan["leverage_settings"]["period_start"])

    def test_bad_leverage_dates_do_not_disable_usage_in_both(self):
        plan = automation.create_plan({"INPUT_MODE": "both", "INPUT_START": "not-a-date"}, NOW)
        self.assertTrue(plan["usage"])
        self.assertTrue(plan["leverage"])
        self.assertIsNotNone(plan["leverage_error"])

    def test_usage_ignores_leverage_configuration_and_retains_its_period(self):
        plan = automation.create_plan({"INPUT_MODE": "usage", "INPUT_START": "not-a-date", "PHRASE_TMS_REGION": "invalid"}, NOW)
        self.assertTrue(plan["dates_are_leverage_only"])
        self.assertIsNone(plan["leverage_error"])

    def test_six_existing_dashboard_email_secret_names_cannot_be_repurposed(self):
        for secret in automation.DASHBOARD_EMAIL_SECRETS:
            plan = automation.create_plan({"INPUT_MODE": "both", "INPUT_CREDENTIAL_SECRET": secret}, NOW)
            self.assertIsNotNone(plan["leverage_error"])
            self.assertTrue(plan["usage"])
            self.assertEqual("PHRASE_TMS_AUTHORIZATION", plan["credential_secret"])

    def test_existing_custom_secret_and_repository_defaults_are_reused(self):
        env = {"PHRASE_REPORT_MODE": "both", "PHRASE_TMS_CREDENTIAL_SECRET": "existing_rest_token",
               "PHRASE_TMS_AUTH_SCHEME": "Bearer", "PHRASE_TMS_REGION": "US", "PHRASE_LEVERAGE_PERIOD": "all",
               "PHRASE_LEVERAGE_PROJECT_UIDS": "P1,P2", "PHRASE_LEVERAGE_CLIENT_IDS": "7,8",
               "PHRASE_LEVERAGE_WORKFLOW_LEVELS": "2,1", "PHRASE_LEVERAGE_ONLY_OWNER_ORG": "true"}
        plan = automation.create_plan(env, NOW)
        self.assertEqual("EXISTING_REST_TOKEN", plan["credential_secret"])
        self.assertEqual("https://us.cloud.memsource.com/web", plan["leverage_settings"]["base_url"])
        self.assertTrue(plan["leverage_settings"]["only_owner_org"])
        args = automation.leverage_arguments(plan)
        self.assertIn("Bearer", args)
        self.assertEqual(2, args.count("--project-uid"))
        self.assertEqual(2, args.count("--report-workflow-level"))
        self.assertIn("--only-owner-org", args)

    def test_inherited_owner_flag_can_be_overridden_by_legacy_manual_input(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage", "PHRASE_LEVERAGE_ONLY_OWNER_ORG": "true", "INPUT_ONLY_OWNER_ORG": "false"}, NOW)
        self.assertFalse(plan["leverage_settings"]["only_owner_org"])

    def test_invalid_scopes_are_leverage_failures_only(self):
        for env in ({"INPUT_PROJECT_UIDS": "P1;command"}, {"INPUT_CLIENT_IDS": "not-integer"},
                    {"INPUT_WORKFLOW_LEVELS": "0"}, {"INPUT_REGION": "other"}, {"INPUT_ONLY_OWNER_ORG": "other"}):
            plan = automation.create_plan({"INPUT_MODE": "both", **env}, NOW)
            self.assertTrue(plan["usage"])
            self.assertIsNotNone(plan["leverage_error"])

    def test_artifacts_default_to_private_repositories_and_can_be_disabled(self):
        self.assertFalse(automation.create_plan({"INPUT_MODE": "leverage"}, NOW)["upload_artifacts"])
        self.assertTrue(automation.create_plan({"INPUT_MODE": "leverage", "REPOSITORY_PRIVATE": "true"}, NOW)["upload_artifacts"])
        self.assertFalse(automation.create_plan({"INPUT_MODE": "leverage", "REPOSITORY_PRIVATE": "true", "PHRASE_LEVERAGE_ARTIFACTS": "false"}, NOW)["upload_artifacts"])
        self.assertTrue(automation.create_plan({"INPUT_MODE": "leverage", "PHRASE_LEVERAGE_ARTIFACTS": "true"}, NOW)["upload_artifacts"])

    def test_integer_errors_name_the_exact_input_or_actions_variable(self):
        for key, source in (("INPUT_CLIENT_IDS", "workflow input client_ids"),
                            ("INPUT_WORKFLOW_LEVELS", "workflow input report_workflow_levels"),
                            ("PHRASE_LEVERAGE_CLIENT_IDS", "Actions variable PHRASE_LEVERAGE_CLIENT_IDS"),
                            ("PHRASE_LEVERAGE_WORKFLOW_LEVELS", "Actions variable PHRASE_LEVERAGE_WORKFLOW_LEVELS")):
            with self.subTest(key=key):
                invalid = "untrusted-private-value"
                plan = automation.create_plan({"INPUT_MODE": "both", key: invalid}, NOW)
                self.assertIn(source, plan["leverage_error"])
                self.assertNotIn(invalid, plan["leverage_error"])
                self.assertTrue(plan["usage"])
                self.assertEqual("2026-01-01T00:00:00Z", plan["leverage_period"]["period_start"])
                self.assertEqual("2026-11-03T16:00:00Z", plan["leverage_period"]["period_end"])

    def test_blank_numeric_inputs_inherit_repository_variables_or_safe_defaults(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage", "INPUT_CLIENT_IDS": " ", "INPUT_WORKFLOW_LEVELS": " "}, NOW)
        self.assertIsNone(plan["leverage_error"])
        self.assertEqual((), plan["leverage_settings"]["client_ids"])
        self.assertEqual((1,), plan["leverage_settings"]["report_workflow_levels"])
        plan = automation.create_plan({"INPUT_MODE": "leverage", "INPUT_CLIENT_IDS": "", "PHRASE_LEVERAGE_CLIENT_IDS": "Client Name"}, NOW)
        self.assertIn("Actions variable PHRASE_LEVERAGE_CLIENT_IDS", plan["leverage_error"])

    def test_valid_numeric_overrides_and_workflow_range_validation(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage", "INPUT_CLIENT_IDS": " 12345, 67890 ",
                                       "PHRASE_LEVERAGE_CLIENT_IDS": "invalid-inherited-value", "INPUT_WORKFLOW_LEVELS": "2,1,2"}, NOW)
        self.assertIsNone(plan["leverage_error"])
        self.assertEqual((12345, 67890), plan["leverage_settings"]["client_ids"])
        self.assertEqual((1, 2), plan["leverage_settings"]["report_workflow_levels"])
        for value in ("[1,2]", "Translation", "all", "0", "16", ","):
            with self.subTest(value=value):
                plan = automation.create_plan({"INPUT_MODE": "both", "INPUT_WORKFLOW_LEVELS": value}, NOW)
                self.assertIn("workflow input report_workflow_levels", plan["leverage_error"])
                self.assertTrue(plan["usage"])

    def test_scope_failure_logs_resolved_period_and_makes_no_api_requests(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage", "INPUT_WORKFLOW_LEVELS": "Translation"}, NOW)
        with tempfile.TemporaryDirectory() as directory, patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")), \
                redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
            self.assertEqual(2, automation.run_leverage(plan, directory))
        self.assertIn("2026-01-01T00:00:00Z <= analysis_created_at < 2026-11-03T16:00:00Z", output.getvalue())
        self.assertIn("workflow input report_workflow_levels", errors.getvalue())
        self.assertIn("Failed API requests: 0", output.getvalue())

    def test_plan_contains_secret_names_only_and_no_credential_values(self):
        value = "private-runtime-authorization-value"
        plan = automation.create_plan({"INPUT_MODE": "both", "PHRASE_TMS_AUTHORIZATION": value, "EMAIL_PASSWORD": value}, NOW)
        self.assertNotIn(value, json.dumps(plan))


class ExecutionTests(unittest.TestCase):
    def usage_fixture(self, directory):
        root = Path(directory)
        shutil.copyfile(ROOT / "main.py", root / "main.py")
        shutil.copyfile(ROOT / "mapping_settings.json", root / "mapping_settings.json")
        project = json.loads((root / "mapping_settings.json").read_text(encoding="utf-8"))["products"][0]["projects"]["de-DE"]
        ai = response(200, {"data": {"rows": [[project, None, None, 2.5], ["Orphan es-ES 99", None, None, 1]]}})
        words = response(200, {"data": {"rows": [[project, None, None, None, "100"], ["Orphan es-ES 99", None, None, None, "20"]]}})
        env = {"PHRASE_API_URL": "https://example.invalid/ai", "PHRASE_REFERER": "https://example.invalid/ref",
               "PHRASE_API_URL_WORDS": "https://example.invalid/words", "PHRASE_REFERER_WORDS": "https://example.invalid/ref"}
        return root, ai, words, env

    def test_usage_executes_original_writer_and_preserves_all_files_and_values(self):
        plan = automation.create_plan({}, NOW)
        with tempfile.TemporaryDirectory() as directory:
            root, ai, words, env = self.usage_fixture(directory)
            with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                    patch("requests.get", side_effect=[ai, words]) as get, redirect_stdout(StringIO()) as log:
                self.assertEqual(0, automation.run_usage(plan, root))
            self.assertEqual(2, get.call_count)
            self.assertTrue(all(call.kwargs["params"] == {"date": "thisyear"} for call in get.call_args_list))
            self.assertEqual(5, len(list(root.glob("*.xlsx"))))
            book = openpyxl.load_workbook(next(root.glob("Master_Phrase_Usage_Stats_*.xlsx")))
            self.assertEqual((3.5, 120), list(book["Data"].values)[-1][3:])
            book.close()
            summary = json.loads((root / "reports/automation/usage_run.json").read_text(encoding="utf-8"))
            self.assertEqual(("success", 2, 1, 0, 5), (summary["status"], summary["projects_processed"], summary["successful_extractions"], summary["failed_api_requests"], summary["output_files_created"]))
            self.assertIn("Analyses found: not available", log.getvalue())

    def test_wordcount_fallback_still_generates_and_delivers_usage_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root, ai, _, env = self.usage_fixture(directory)
            with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                    patch("requests.get", side_effect=[ai, response(503, {})]), redirect_stdout(StringIO()):
                self.assertEqual(0, automation.run_usage(automation.create_plan({}, NOW), root))
            summary = json.loads((root / "reports/automation/usage_run.json").read_text(encoding="utf-8"))
            self.assertEqual("partial", summary["status"])
            self.assertEqual(1, summary["failed_api_requests"])
            self.assertEqual(5, summary["output_files_created"])

    def test_fatal_usage_fetch_does_not_report_success_without_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, env = self.usage_fixture(directory)
            with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                    patch("requests.get", return_value=response(401, {})), redirect_stdout(StringIO()):
                self.assertEqual(2, automation.run_usage(automation.create_plan({}, NOW), root))
            summary = json.loads((root / "reports/automation/usage_run.json").read_text(encoding="utf-8"))
            self.assertEqual(("failed", 0, 1), (summary["status"], summary["output_files_created"], summary["failed_api_requests"]))

    def test_usage_transport_exception_cannot_leak_embedded_dashboard_jwt(self):
        token = "eyJfixture.payload.signature"
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, env = self.usage_fixture(directory)
            env["PHRASE_API_URL"] = "https://example.invalid/embed/" + token
            with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                    patch("requests.get", side_effect=requests.Timeout("Token in request path /embed/" + token)), redirect_stdout(StringIO()) as log:
                self.assertEqual(2, automation.run_usage(automation.create_plan({}, NOW), root))
            self.assertNotIn(token, log.getvalue())
            self.assertIn("details suppressed", log.getvalue())

    def test_usage_schema_exception_does_not_print_sensitive_response_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, env = self.usage_fixture(directory)
            bad = response(200, {"data": {"rows": [["Project", None, None, "client-confidential-column-value"]]}})
            with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {"dotenv": None}), \
                    patch("requests.get", return_value=bad), redirect_stdout(StringIO()) as log:
                self.assertEqual(2, automation.run_usage(automation.create_plan({}, NOW), root))
            self.assertNotIn("client-confidential-column-value", log.getvalue())
            self.assertIn("details suppressed", log.getvalue())

    def test_leverage_missing_secret_fails_clearly_while_both_plan_keeps_usage(self):
        plan = automation.create_plan({"INPUT_MODE": "both"}, NOW)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("No live requests")), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()) as error:
            self.assertEqual(2, automation.run_leverage(plan, Path(directory)))
            self.assertIn("REST secret", error.getvalue())
            self.assertTrue(plan["usage"])
            summary = json.loads((Path(directory) / "reports/automation/leverage_run.json").read_text(encoding="utf-8"))
            self.assertIsNone(summary["projects_processed"])
            self.assertEqual(0, summary["failed_api_requests"])

    def test_leverage_uses_existing_cli_and_logs_safe_partial_statistics(self):
        plan = automation.create_plan({"INPUT_MODE": "both", "INPUT_CREDENTIAL_SECRET": "EXISTING_REST"}, NOW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def fake_main(args):
                self.assertNotIn("private-token", args)
                self.assertIn("2026-11-03T16:00:00Z", args)
                output = Path(args[args.index("--output") + 1]); output.mkdir(parents=True)
                (output / "manifest.json").write_text(json.dumps({"status": "partial", "run_statistics": {
                    "projects_processed": 3, "analyses_found": 10, "successful_extractions": 8,
                    "missing_analyses": 2, "failed_api_requests": 4}}), encoding="utf-8")
                (output / "Project_Phrase_Leverage_Stats_test.csv").write_text("headers\n", encoding="utf-8")
                return 2
            with patch.dict(os.environ, {"PHRASE_TMS_AUTHORIZATION": "private-token"}, clear=True), \
                    patch("leverage_main.main", side_effect=fake_main), redirect_stdout(StringIO()) as log:
                self.assertEqual(2, automation.run_leverage(plan, root))
            for label in ("Projects processed: 3", "Analyses found: 10", "Successful extractions: 8", "Missing valid analyses (job/scope): 2", "Failed API requests: 4", "Output files created: 2"):
                self.assertIn(label, log.getvalue())
            self.assertNotIn("private-token", log.getvalue())

    def test_leverage_unexpected_errors_do_not_print_tokens(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage"}, NOW)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"PHRASE_TMS_AUTHORIZATION": "private-token"}, clear=True), \
                patch("leverage_main.main", side_effect=RuntimeError("private-token")), redirect_stdout(StringIO()) as log, redirect_stderr(StringIO()) as err:
            self.assertEqual(2, automation.run_leverage(plan, Path(directory)))
        self.assertNotIn("private-token", log.getvalue() + err.getvalue())

    def test_failed_rerun_does_not_reuse_stale_manifest_or_outputs(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage"}, NOW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "reports/leverage"; folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({"status": "complete", "run_statistics": {"projects_processed": 99}}), encoding="utf-8")
            with patch.dict(os.environ, {"PHRASE_TMS_AUTHORIZATION": "private-token"}, clear=True), \
                    patch("leverage_main.main", return_value=2), redirect_stdout(StringIO()):
                self.assertEqual(2, automation.run_leverage(plan, root))
            summary = json.loads((root / "reports/automation/leverage_run.json").read_text(encoding="utf-8"))
            self.assertIsNone(summary["projects_processed"])
            self.assertEqual(0, summary["output_files_created"])

    def test_leverage_requires_a_fresh_complete_manifest_for_success(self):
        plan = automation.create_plan({"INPUT_MODE": "leverage"}, NOW)
        for manifest, expected in ((None, 2), ({"status": "writing", "core_complete": False}, 2),
                                   ({"status": "complete", "core_complete": True, "run_statistics": {"projects_processed": 1, "failed_api_requests": 0}}, 0)):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def fake_main(args):
                    if manifest is not None:
                        output = Path(args[args.index("--output") + 1]); output.mkdir(parents=True)
                        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                    return 0
                with patch.dict(os.environ, {"PHRASE_TMS_AUTHORIZATION": "private-token"}, clear=True), \
                        patch("leverage_main.main", side_effect=fake_main), redirect_stdout(StringIO()):
                    self.assertEqual(expected, automation.run_leverage(plan, root))

    def test_summary_reports_leverage_failure_and_usage_success_without_masking(self):
        env = {"SELECTED_MODE": "both", "CONFIGURE_RESULT": "success", "USAGE_RESULT": "success", "LEVERAGE_RESULT": "failure",
               "USAGE_SUMMARY": json.dumps({"period": "date=thisyear", "projects_processed": 2}),
               "LEVERAGE_SUMMARY": json.dumps({"period": "UTC [start,end)", "failed_api_requests": 1})}
        with redirect_stdout(StringIO()) as log:
            self.assertEqual(2, automation.finish(env))
        self.assertIn("| usage | success | not available | 2 |", log.getvalue())
        self.assertIn("| leverage | failure |", log.getvalue())
        self.assertIn("Usage period:", log.getvalue())

    def test_summary_skipped_unselected_jobs_do_not_fail_usage(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(0, automation.finish({"SELECTED_MODE": "usage", "CONFIGURE_RESULT": "success", "USAGE_RESULT": "success", "LEVERAGE_RESULT": "skipped"}))

    def test_unexpected_skip_of_selected_process_is_a_failure(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(2, automation.finish({"SELECTED_MODE": "both", "CONFIGURE_RESULT": "success", "USAGE_RESULT": "success", "LEVERAGE_RESULT": "skipped"}))

    def test_redactor_handles_full_secrets_and_jwt_fragments(self):
        token = "eyJfixture.payload.signature"
        with patch.dict(os.environ, {"PHRASE_API_URL": "https://example.invalid/embed/" + token,
                                    "PHRASE_TMS_AUTHORIZATION": "Bearer private-token"}, clear=True):
            text = automation.redact("URL=https://example.invalid/embed/" + token + " fragment=" + token + " private-token")
        self.assertNotIn(token, text)
        self.assertNotIn("private-token", text)
        self.assertIn("REDACTED", text)

    def test_request_failures_count_retries_and_transport_attempts(self):
        from leverage.client import TmsClient
        from leverage.config import Settings
        from tests.fakes import FakeSession
        outcomes = iter([response(429, {}, {"Retry-After": "0"}), response(503, {}), response(200, {})])
        client = TmsClient(Settings(authorization="ApiToken fixture-secret"), FakeSession(lambda p, q: next(outcomes)), Mock(), lambda: 0)
        client.get("/api2/v1/projects")
        self.assertEqual((3, 2), (client.request_count, client.failed_request_count))


if __name__ == "__main__":
    unittest.main()
