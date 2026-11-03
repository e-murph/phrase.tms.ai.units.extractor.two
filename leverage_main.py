"""Run existing-analysis retrieval without importing or changing the AI scraper."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from leverage.client import TmsClient
from leverage.config import ConfigurationError, Settings, timestamp
from leverage.export import export
from leverage.extractor import Extraction


def parser():
    result = argparse.ArgumentParser(description="Retrieve existing Phrase TMS analyses using GET requests only.")
    result.add_argument("--credential-env", default="PHRASE_TMS_AUTHORIZATION",
                        help="Environment variable containing an existing REST token/header; never pass the token itself.")
    result.add_argument("--auth-scheme", choices=("ApiToken", "Bearer"), default="ApiToken")
    result.add_argument("--base-url", default=os.getenv("PHRASE_TMS_BASE_URL", "https://cloud.memsource.com/web"))
    result.add_argument("--period", choices=("thisyear", "all"), default="thisyear")
    result.add_argument("--start", help="Inclusive ISO date or timezone-aware timestamp (UTC).")
    result.add_argument("--end", help="Exclusive ISO date or timezone-aware timestamp; defaults to the frozen run time.")
    result.add_argument("--project-uid", action="append", default=[], help="Exact UID; repeat to select multiple projects.")
    result.add_argument("--project-name", action="append", default=[], help="Exact name, not record identity.")
    result.add_argument("--client-id", action="append", default=[], type=int, help="Numeric client ID, not UID.")
    result.add_argument("--client-name", action="append", default=[], help="Exact client name.")
    result.add_argument("--exclude-archived", action="store_true")
    result.add_argument("--only-owner-org", action="store_true", help="Restrict analysis ownership to this organization.")
    result.add_argument("--no-download-json", action="store_true", help="Skip supplemental native JSON attachments.")
    result.add_argument("--summary-type", action="append", help="Default PreAnalyse; other types remain separate summary groups.")
    result.add_argument("--count-basis", choices=("source", "target"), default="source")
    result.add_argument("--report-workflow-level", action="append", type=int, choices=range(1, 16),
                        help="Workflow level for reporting; repeat for separate scopes. Default 1. Inventory still covers all steps.")
    result.add_argument("--category-tolerance", default="0.000001", help="Absolute category-total reconciliation tolerance; no count rounding.")
    result.add_argument("--requests-per-second", type=float, default=2)
    result.add_argument("--retries", type=int, default=3)
    result.add_argument("--max-pages", type=int, default=20000)
    result.add_argument("--output", type=Path, default=Path("reports/leverage"))
    return result


def main(argv=None):
    # Match the existing application's optional local .env convention.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    args = parser().parse_args(argv)
    client = None
    try:
        credential = os.getenv(args.credential_env, "").strip()
        if args.credential_env.upper() in {"EMAIL_USERNAME", "EMAIL_PASSWORD"}:
            raise ConfigurationError("Email credentials cannot authenticate the TMS REST API.")
        if not credential:
            raise ConfigurationError(
                "No TMS REST credential is configured. The existing PHRASE_API_URL/PHRASE_REFERER "
                "secrets are dashboard URLs. Map an existing REST authorization secret to an environment "
                "variable and select its name with --credential-env. Do not paste credentials into commands or chat.")
        if not credential.startswith(("ApiToken ", "Bearer ")):
            credential = args.auth_scheme + " " + credential
        now = datetime.now(timezone.utc)
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0) if args.period == "thisyear" else None
        if args.start:
            start = timestamp(args.start)
        end = timestamp(args.end) if args.end else now
        settings = Settings(
            authorization=credential, base_url=args.base_url, start=start, end=end,
            project_uids=tuple(args.project_uid), project_names=tuple(args.project_name),
            client_ids=tuple(args.client_id), client_names=tuple(args.client_name),
            include_archived=not args.exclude_archived, only_owner_org=args.only_owner_org,
            download_json=not args.no_download_json, summary_types=tuple(args.summary_type or ["PreAnalyse"]),
            count_basis=args.count_basis, requests_per_second=args.requests_per_second,
            retries=args.retries, max_pages=args.max_pages,
            report_workflow_levels=tuple(args.report_workflow_level or [1]), category_tolerance=args.category_tolerance,
        )
        mapping_path = Path(__file__).with_name("mapping_settings.json")
        mapping = {}
        if mapping_path.exists():
            data = json.loads(mapping_path.read_text(encoding="utf-8"))
            mapping = {name: product["product_name"] for product in data["products"] for name in product["projects"].values()}
        client = TmsClient(settings)
        extraction = Extraction(client, mapping).run()
        manifest = export(extraction, args.output)
        print("Leverage retrieval: " + manifest["status"] + "; " +
              str(manifest["record_counts"]["selected_jobs"]) + " selected job/type records. See manifest.json and coverage.csv.")
        errors = {row["code"]: row["message"] for row in extraction.tables["coverage"].values()
                  if row["impact"] in {"core", "metadata"}}
        for code, message in sorted(errors.items())[:5]:
            print(code + ": " + message, file=sys.stderr)
        if not manifest["report_validation"]["valid"]:
            print("Leverage report validation failed; inspect " + manifest["report_files"]["validation"] + ".", file=sys.stderr)
        return 0 if manifest["core_complete"] else 2
    except ConfigurationError as error:
        print("Leverage configuration error: " + str(error), file=sys.stderr)
        return 2
    except Exception:
        # requests exceptions and server payloads can contain credentials. Do not
        # print exception text or tracebacks at this boundary.
        print("Leverage retrieval/export failed. Check output permissions and the configured REST access; AI reporting is independent.", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
