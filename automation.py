"""GitHub Actions planning and safe execution; existing report writers stay independent."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
import runpy
import sys
from unittest.mock import patch

from leverage.config import ConfigurationError, Settings, iso, timestamp


MODES = {"usage", "leverage", "both"}
DASHBOARD_EMAIL_SECRETS = {"PHRASE_API_URL", "PHRASE_API_URL_WORDS", "PHRASE_REFERER",
                           "PHRASE_REFERER_WORDS", "EMAIL_USERNAME", "EMAIL_PASSWORD"}


def selected(env, input_name, variable_name, default=""):
    value = env.get(input_name, "").strip()
    return value if value and value != "configured" else env.get(variable_name, "").strip() or default


def csv_values(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def integer_values(env, input_name, variable_name, field, default="", workflow=False):
    value = selected(env, input_name, variable_name, default)
    manual = env.get(input_name, "").strip()
    source = ("workflow input " + field if manual and manual != "configured" else
              "Actions variable " + variable_name if env.get(variable_name, "").strip() else
              "default for " + field)
    help_text = ("Use workflow numbers from 1 to 15, for example 1 or 1,2; do not enter workflow names, all, or brackets."
                 if workflow else
                 "Use numeric TMS client IDs, for example 12345 or 12345,67890; do not enter client names or UIDs. "
                 "To include all accessible clients, leave both client_ids and PHRASE_LEVERAGE_CLIENT_IDS blank.")
    try:
        result = tuple(int(item) for item in csv_values(value))
    except ValueError:
        raise ConfigurationError("Invalid " + source + ". " + help_text) from None
    if workflow and (not result or any(not 1 <= level <= 15 for level in result)):
        raise ConfigurationError("Invalid " + source + ". " + help_text)
    return result


def create_plan(env, now=None):
    now = now or datetime.now(timezone.utc)
    mode = selected(env, "INPUT_MODE", "PHRASE_REPORT_MODE", "usage").lower()
    if mode not in MODES:
        raise ConfigurationError("Reporting mode must be usage, leverage, or both.")
    plan = {"mode": mode, "usage": mode in {"usage", "both"}, "leverage": mode in {"leverage", "both"},
            "created_at": iso(now), "usage_period": "dashboard date=thisyear",
            "dates_are_leverage_only": bool(env.get("INPUT_START") or env.get("INPUT_END")),
            "credential_secret": "PHRASE_TMS_AUTHORIZATION", "leverage_error": None, "upload_artifacts": False}
    if not plan["leverage"]:
        return plan
    try:
        region = selected(env, "INPUT_REGION", "PHRASE_TMS_REGION", "EU")
        auth = selected(env, "INPUT_AUTH_SCHEME", "PHRASE_TMS_AUTH_SCHEME", "ApiToken")
        period = selected(env, "INPUT_PERIOD", "PHRASE_LEVERAGE_PERIOD", "thisyear")
        if region not in {"EU", "US"} or auth not in {"ApiToken", "Bearer"} or period not in {"thisyear", "all"}:
            raise ConfigurationError("Invalid leverage region, authentication type, or period configuration.")
        start = timestamp(env["INPUT_START"].strip()) if env.get("INPUT_START", "").strip() else \
            now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0) if period == "thisyear" else None
        end = timestamp(env["INPUT_END"].strip()) if env.get("INPUT_END", "").strip() else now
        # Preserve resolved dates even if a later scope input fails validation.
        plan["leverage_period"] = {"period_start": iso(start), "period_end": iso(end)}
        clients = integer_values(env, "INPUT_CLIENT_IDS", "PHRASE_LEVERAGE_CLIENT_IDS", "client_ids")
        levels = integer_values(env, "INPUT_WORKFLOW_LEVELS", "PHRASE_LEVERAGE_WORKFLOW_LEVELS",
                                "report_workflow_levels", "1", workflow=True)
        owner = selected(env, "INPUT_ONLY_OWNER_ORG", "PHRASE_LEVERAGE_ONLY_OWNER_ORG", "false").lower()
        if owner not in {"true", "false"}:
            raise ConfigurationError("Analysis ownership configuration must be true or false.")
        settings = Settings(
            authorization="ApiToken configuration-validation", start=start, end=end,
            base_url="https://cloud.memsource.com/web" if region == "EU" else "https://us.cloud.memsource.com/web",
            project_uids=tuple(csv_values(selected(env, "INPUT_PROJECT_UIDS", "PHRASE_LEVERAGE_PROJECT_UIDS"))),
            client_ids=clients, report_workflow_levels=levels,
            only_owner_org=owner == "true",
        )
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in settings.project_uids):
            raise ConfigurationError("Project scope must contain comma-separated TMS UIDs.")
        plan["leverage_settings"] = {**settings.public(), "auth_scheme": auth, "period": period}
        secret = selected(env, "INPUT_CREDENTIAL_SECRET", "PHRASE_TMS_CREDENTIAL_SECRET", "PHRASE_TMS_AUTHORIZATION").upper()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", secret) or secret in DASHBOARD_EMAIL_SECRETS:
            raise ConfigurationError("Select an existing REST-capable secret by name; dashboard/email values cannot be used for leverage.")
        plan["credential_secret"] = secret
    except ConfigurationError as error:
        # Configuration errors affect only the leverage job in mode=both.
        plan["leverage_error"] = str(error)
    artifact_policy = env.get("PHRASE_LEVERAGE_ARTIFACTS", "auto").strip().lower() or "auto"
    if artifact_policy not in {"auto", "true", "false"}:
        plan["leverage_error"] = "PHRASE_LEVERAGE_ARTIFACTS must be auto, true, or false."
    plan["upload_artifacts"] = artifact_policy == "true" or (artifact_policy == "auto" and env.get("REPOSITORY_PRIVATE", "false").lower() == "true")
    return plan


def redact(text):
    values = [os.environ.get(key, "") for key in DASHBOARD_EMAIL_SECRETS | {"PHRASE_TMS_AUTHORIZATION"}]
    auth = os.environ.get("PHRASE_TMS_AUTHORIZATION", "")
    if auth.startswith(("ApiToken ", "Bearer ")):
        values.append(auth.split(" ", 1)[1])
    for value in sorted(set(values), key=len, reverse=True):
        if value:
            text = text.replace(value, "[REDACTED]")
    return re.sub(r"eyJ[A-Za-z0-9_-]+(?:\.|%2[Ee])[A-Za-z0-9_-]+(?:\.|%2[Ee])[A-Za-z0-9_-]+", "[REDACTED-JWT]", text)


def github_output(key, value):
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(key + "=" + value + "\n")


def log_statistics(summary):
    labels = {"projects_processed": "Projects processed", "analyses_found": "Analyses found",
              "successful_extractions": "Successful extractions", "missing_analyses": "Missing valid analyses (job/scope)",
              "failed_api_requests": "Failed API requests", "output_files_created": "Output files created"}
    print("Selected mode: " + summary["mode"] + "; process: " + summary["process"] + "; status: " + summary["status"])
    print("Reporting period: " + summary["period"])
    for key, label in labels.items():
        value = summary.get(key)
        print(label + ": " + (str(value) if value is not None else "not available"))
    for filename in summary.get("output_files", []):
        print("Created output: " + redact(filename))


def save_summary(summary, root):
    folder = root / "reports" / "automation"
    folder.mkdir(parents=True, exist_ok=True)
    text = redact(json.dumps(summary, sort_keys=True))
    (folder / (summary["process"] + "_run.json")).write_text(text + "\n", encoding="utf-8")
    github_output("summary", text)
    log_statistics(json.loads(text))


def initial_summary(plan, process, period):
    return {"mode": plan["mode"], "process": process, "period": period, "status": "failed",
            "projects_processed": None, "analyses_found": None, "successful_extractions": None,
            "missing_analyses": None, "failed_api_requests": 0, "output_files_created": 0, "output_files": []}


def run_usage(plan, root=None):
    root = Path(root or Path.cwd()).resolve()
    summary = initial_summary(plan, "usage", plan["usage_period"])
    captured = StringIO()
    failed_requests = 0
    calls = 0
    before = {p.name: p.stat().st_mtime_ns for p in root.glob("*.xlsx")}
    fatal = False
    try:
        import requests
        original_get = requests.get

        def observed_get(*args, **kwargs):
            nonlocal calls, failed_requests
            calls += 1
            try:
                response = original_get(*args, **kwargs)
                failed_requests += response.status_code not in (200, 202)
                return response
            except requests.RequestException:
                failed_requests += 1
                raise

        old_directory = Path.cwd()
        try:
            os.chdir(root)
            with redirect_stdout(captured), redirect_stderr(captured), patch("requests.get", side_effect=observed_get):
                runpy.run_path(str(root / "main.py"), run_name="__main__")
        finally:
            os.chdir(old_directory)
    except Exception:
        fatal = True
        print("Usage execution failed; exception payload suppressed to protect dashboard credentials.", file=sys.stderr)
    text = captured.getvalue()
    safe_lines = ["Usage processing error; exception/response details suppressed to protect client data.\n"
                  if "An error occurred:" in line else line for line in text.splitlines(keepends=True)]
    print(redact("".join(safe_lines)), end="")
    output = sorted(p.name for p in root.glob("*.xlsx") if before.get(p.name) != p.stat().st_mtime_ns)
    master = any(name.startswith(("Master_Phrase_Usage_Stats_", "Raw_Phrase_Data_")) for name in output)
    fatal = fatal or not master or "An error occurred:" in text
    match = re.search(r"Merged data for (\d+) total projects", text)
    summary.update(projects_processed=int(match.group(1)) if match else None,
                   successful_extractions=int(master and not fatal), failed_api_requests=failed_requests,
                   output_files=output, output_files_created=len(output), api_requests=calls,
                   status="failed" if fatal else "partial" if failed_requests else "success")
    if not fatal and failed_requests:
        print("Usage retained its existing wordcount fallback; inspect the reported failed request count.")
    save_summary(summary, root)
    return 2 if fatal else 0


def leverage_arguments(plan):
    config = plan["leverage_settings"]
    args = ["--base-url", config["base_url"], "--auth-scheme", config["auth_scheme"], "--period", config["period"]]
    for key, flag in (("period_start", "--start"), ("period_end", "--end")):
        if config.get(key):
            args.extend((flag, config[key]))
    for key, flag in (("project_uids", "--project-uid"), ("client_ids", "--client-id"), ("report_workflow_levels", "--report-workflow-level")):
        for value in config[key]:
            args.extend((flag, str(value)))
    if config["only_owner_org"]:
        args.append("--only-owner-org")
    return args


def run_leverage(plan, root=None):
    root = Path(root or Path.cwd()).resolve()
    config = plan.get("leverage_settings") or plan.get("leverage_period")
    period = ((config.get("period_start") or "all available dates") + " <= analysis_created_at < " +
              config["period_end"] + " (UTC)") if config else "not resolved (leverage configuration failed)"
    summary = initial_summary(plan, "leverage", period)
    error = plan.get("leverage_error")
    if not error and not os.environ.get("PHRASE_TMS_AUTHORIZATION", "").strip():
        error = "The selected REST secret has no runtime value. Configure an existing REST-capable secret; dashboard/email secrets cannot authenticate this endpoint."
    if error:
        print("Leverage configuration failed: " + error, file=sys.stderr)
        save_summary(summary, root)
        return 2
    captured = StringIO()
    folder = root / "reports" / "leverage"
    before = {p.name: p.stat().st_mtime_ns for p in folder.iterdir() if p.is_file()} if folder.exists() else {}
    summary["failed_api_requests"] = None  # Runtime failures without a manifest have unknown request counts.
    try:
        from leverage_main import main as leverage_main
        with redirect_stdout(captured), redirect_stderr(captured):
            code = leverage_main([*leverage_arguments(plan), "--output", str(folder)])
    except Exception:
        code = 2
        print("Leverage execution failed; exception payload suppressed to protect credentials.", file=sys.stderr)
    print(redact(captured.getvalue()), end="")
    files = sorted(p.relative_to(root).as_posix() for p in folder.iterdir()
                   if p.is_file() and before.get(p.name) != p.stat().st_mtime_ns) if folder.exists() else []
    summary.update(output_files=files, output_files_created=len(files))
    try:
        manifest_path = folder / "manifest.json"
        if before.get("manifest.json") == manifest_path.stat().st_mtime_ns:
            raise ValueError("No current manifest was written.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if code == 0 and not manifest.get("core_complete"):
            code = 2
        stats = manifest.get("run_statistics", {})
        summary.update({key: stats.get(key) for key in ("projects_processed", "analyses_found", "successful_extractions", "missing_analyses", "failed_api_requests")})
        summary.update(status="success" if code == 0 and manifest.get("status") == "complete" else "partial" if code == 0 else "failed")
    except (OSError, ValueError):
        code = 2
    save_summary(summary, root)
    return code


def finish(env):
    mode = env.get("SELECTED_MODE") or "configuration failed"
    lines = ["## Phrase reporting", "", "Selected mode: " + mode, "",
             "| Process | Job result | Data status | Projects | Analyses | Successful extractions | Missing analyses | Failed API requests | Output files |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    failed = env.get("CONFIGURE_RESULT") != "success"
    periods = []
    for process in ("usage", "leverage"):
        result = env.get(process.upper() + "_RESULT", "skipped")
        failed = failed or result in {"failure", "cancelled"}
        if mode in {process, "both"} and result != "success":
            failed = True
        try:
            summary = json.loads(env.get(process.upper() + "_SUMMARY") or "{}")
        except ValueError:
            summary = {}
        failed = failed or summary.get("status") == "failed"
        cells = [process, result, summary.get("status", "not available")] + [str(summary.get(key)) if summary.get(key) is not None else "n/a" for key in
                ("projects_processed", "analyses_found", "successful_extractions", "missing_analyses", "failed_api_requests", "output_files_created")]
        lines.append("| " + " | ".join(cells) + " |")
        if summary.get("period"):
            periods.append(process.capitalize() + " period: " + summary["period"])
    if periods:
        lines.extend(["", *periods])
    text = redact("\n".join(lines) + "\n")
    print(text)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(text)
    return 2 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan or execute isolated GitHub reporting jobs.")
    parser.add_argument("command", choices=("plan", "usage", "leverage", "summary"))
    command = parser.parse_args(argv).command
    try:
        if command == "summary":
            return finish(os.environ)
        if command == "plan":
            plan = create_plan(os.environ)
            for key in ("mode", "usage", "leverage", "credential_secret", "upload_artifacts"):
                value = str(plan[key]).lower() if type(plan[key]) is bool else plan[key]
                github_output(key, value)
            github_output("configuration", json.dumps(plan, sort_keys=True))
            print("Selected mode: " + plan["mode"])
            if plan["usage"]:
                print("Usage reporting period: " + plan["usage_period"] + "; existing dashboard period preserved.")
            if plan["dates_are_leverage_only"]:
                print("Optional start/end dates apply only to leverage; usage retains date=thisyear.")
            if plan["leverage"]:
                settings = plan.get("leverage_settings") or plan.get("leverage_period")
                if settings:
                    print("Leverage reporting period (UTC): " + (settings.get("period_start") or "unbounded") + " inclusive to " + settings["period_end"] + " exclusive.")
                else:
                    print("Leverage reporting period: not resolved (configuration failed).")
                if plan["leverage_error"]:
                    print("Leverage configuration error: " + plan["leverage_error"], file=sys.stderr)
                print("Leverage artifact upload: " + ("enabled" if plan["upload_artifacts"] else "disabled by repository confidentiality policy"))
            return 0
        plan = json.loads(os.environ.get("REPORT_CONFIGURATION", "{}"))
        if plan.get("mode") not in MODES:
            raise ConfigurationError("Missing or invalid reporting plan; run the configure job first.")
        if not plan.get(command):
            print("Process not selected by the reporting mode; nothing executed.")
            return 0
        return run_usage(plan) if command == "usage" else run_leverage(plan)
    except ConfigurationError as error:
        print("Reporting configuration error: " + str(error), file=sys.stderr)
        return 2
    except Exception:
        print("Reporting automation failed; exception payload suppressed. Inspect the process status and configuration.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
