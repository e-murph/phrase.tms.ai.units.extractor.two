# GitHub Actions reporting

The original **Phrase AI Scraper** workflow is the single scheduled entry point. It now supports `usage`, `leverage`, and `both`. The default remains **usage**, so no repository setting change is required to retain existing automated AI reporting. The schedule remains `0 16 * * 0`: Sundays at 16:00 UTC.

## Review before workflow edits

All three workflow files, secret references, permissions, actions, and dependency configuration were inspected before editing YAML.

| Original configuration | Preserved or integrated behavior |
| --- | --- |
| `scrape_stats.yml`: Sunday 16:00 UTC and manual dispatch | Same name and sole cron; manual mode/date inputs added. |
| Usage job: Ubuntu 22.04, Python 3.10, four dashboard secrets | Same runner/runtime/secrets and unchanged `main.py`; wrapper adds safe logging and failure detection. |
| Two SMTP sends using `EMAIL_USERNAME` / `EMAIL_PASSWORD` | Same complete email configuration, recipients, subjects, attachments, sender, and bodies. |
| Full and product ZIP exports | Same filenames and XLSX membership globs. |
| No usage artifact upload | Usage remains ZIP-by-email; it does not publish new client-data artifacts. |
| Manual `leverage_stats.yml` with a 14-day artifact | Preserved as a compatibility dispatch; calls the shared workflow with `mode=leverage`. Artifact contents/privacy rules are explicit below. |
| `contents: read` and SHA-pinned actions | Preserved; checkout credentials are no longer persisted. |
| Monthly GitHub Actions Dependabot updates | Unchanged. |
| Four original runtime libraries | Same libraries; the validation review adds security minimums for requests and its existing HTTP/trust dependencies. Development YAML validation pins `PyYAML==6.0.3`. |

## Manual execution

1. Open repository **Actions → Phrase AI Scraper → Run workflow**.
2. Select a branch and `mode`:
   - `usage`: existing AI/wordcount XLSX reports and the two existing ZIP emails.
   - `leverage`: existing-analysis retrieval, validation, and leverage reports only; no usage requests or emails.
   - `both`: independent usage and leverage jobs in one run. Neither depends on the other's success.
3. Leave optional fields blank for configured defaults, or provide leverage start/end dates, an existing REST secret **name**, authentication type, region, period, project/client filters, and workflow levels.
4. Read **Reporting outcome** in the run summary. A combined run can show successful usage delivery and failed leverage; the overall run remains failed rather than hiding that failure.

The older **Phrase Leverage Extractor** manual entry point still accepts its previous inputs, including the ownership switch. It calls the same implementation with `mode=leverage` and inherits existing repository/organization secrets. It has no cron, independent implementation, or caller concurrency lock. [GitHub reusable-workflow behavior](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).

## Automatic modes without source edits

Configure nonsecret repository variables under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHRASE_REPORT_MODE` | `usage` | Scheduled mode: `usage`, `leverage`, or `both`. Manual mode selection overrides this. |
| `PHRASE_TMS_CREDENTIAL_SECRET` | `PHRASE_TMS_AUTHORIZATION` | Name of an already available REST-capable repository/organization secret; never its value. |
| `PHRASE_TMS_AUTH_SCHEME` | `ApiToken` | `ApiToken` or `Bearer` for an unprefixed token. A full authorization header keeps its prefix. |
| `PHRASE_TMS_REGION` | `EU` | `EU` or `US`. |
| `PHRASE_LEVERAGE_PERIOD` | `thisyear` | `thisyear` or `all`; the latter removes the lower boundary. |
| `PHRASE_LEVERAGE_PROJECT_UIDS` | blank | Comma-separated project UIDs; blank discovers accessible projects. |
| `PHRASE_LEVERAGE_CLIENT_IDS` | blank | Comma-separated numeric client IDs. |
| `PHRASE_LEVERAGE_WORKFLOW_LEVELS` | `1` | Comma-separated report levels; each gets its own reporting scope. |
| `PHRASE_LEVERAGE_ONLY_OWNER_ORG` | `false` | Restrict analysis ownership, not client/project ownership. |
| `PHRASE_LEVERAGE_ARTIFACTS` | `auto` | `auto`: upload only in private repositories; `false`: disable; `true`: explicitly enable for the repository's audience after reviewing confidentiality. |

For scheduled combined reports, choose `PHRASE_REPORT_MODE=both` and point `PHRASE_TMS_CREDENTIAL_SECRET` at an existing valid REST secret. **No secret is created, overwritten, printed, or copied by this integration.** No workflow settings were changed remotely by development tools.

The six secrets currently identified in this repository are the four embedded-dashboard URL/referer values and the two SMTP credentials. They remain sufficient for usage, but none provides TMS REST authorization. Selecting leverage with only those six secrets fails clearly. Usage in `both` still runs. Dashboard/email secret names are rejected as REST credential sources; embedded JWT URLs are never repurposed as Bearer tokens. Live leverage requires a securely provisioned, valid REST credential and appropriate project/analysis access.

## Dates and timezone

The hosted jobs explicitly use UTC. Start is inclusive; end is exclusive. A date such as `2026-11-01` means midnight UTC; full timestamps must include a timezone and are converted to UTC.

- Usage preserves its existing dashboard request, `date=thisyear`. No date override or new API parameter is invented for the embedded dashboards. Start/end inputs are leverage-only, and this distinction is printed in logs, including in `both` mode.
- Leverage defaults to January 1 of the run's UTC year through one frozen configuration-job timestamp. That same exact end timestamp is passed to extraction even when jobs start later. Explicit start/end override the respective boundaries. `period=all` has no default lower boundary.
- Leverage filters analysis creation dates, not job/project creation or translation throughput. Matching labels do not guarantee that dashboard usage and analysis volumes describe the same events.

## Jobs, failures, and repeatability

`configure` resolves mode and safe metadata with no credentials. `build` executes the unchanged usage writer. `leverage` invokes the existing read-only leverage entry point. Both process jobs depend only on configuration. `reporting_summary` runs after success, failure, or skipped jobs, publishes counts, and preserves failures in its exit status.

Leverage-only configuration errors are retained for the leverage job rather than failing configuration and preventing usage in `both`. An invalid mode fails configuration because no process can safely be selected. Missing/expired REST credentials, denied analyses, failed validation, nonzero extraction exits, and unexpectedly skipped selected jobs remain visible failures. Partial leverage reports/coverage can still be uploaded when artifact policy permits; they never enter usage ZIP globs. A failed rerun does not reuse stale counts or claim older files as newly created.

Usage keeps its existing wordcount HTTP fallback and reports it as partial, without changing workbook values or blocking its existing ZIP delivery. Fatal usage errors or a missing master/raw output now fail before attempting to email nonexistent reports. Email steps only run for archives that were actually created. A failure sending the full archive does not prevent a separate attempt to deliver a successfully created product archive; delivery errors still fail the job.

Configuration and summary jobs have 5-minute limits, usage 20 minutes, leverage 60 minutes, and offline tests 15 minutes. One reporting run per repository is active at a time, with `cancel-in-progress: false`, including calls from the compatibility workflow. An active run is not canceled to start another report. GitHub's default concurrency policy permits one pending run; a newer pending run can replace an older pending run. [GitHub concurrency reference](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

Usage and leverage run in separate hosted workspaces. Each explicit usage run may send its normal emails; concurrency is not an email deduplication service. Repeatable leverage runs replace current/same-profile/day files with stable business-record IDs; separate run/attempt artifact names identify snapshots. Do not concatenate snapshots as transaction records. No generated workbook, CSV, JSON, ZIP, or diagnostic file is committed to Git. Fresh hosted checkouts avoid leftover local reports being picked up by ZIP globs.

## Logs and run summaries

Each selected process logs mode, period, project count, analysis count, successful extractions, missing analyses, failed API attempts, and created filenames/count. Unknown/not-applicable statistics are explicitly marked, not fabricated as zero.

- Usage project count matches the existing report-row count because the dashboard data has no project UID. Successful extraction is a completed master/raw dataset; analysis counts and missing analyses are not applicable.
- Leverage analyses found counts discovered analysis metadata, including out-of-period/inaccessible references. Successful extractions counts in-period job-analysis records with available raw totals, not distinct physical source files. Missing valid analyses is a job/reporting-scope count from validated project summaries.
- Failed API requests counts actual failed HTTP/transport/JSON attempts, including recovered retries. A configuration failure before a request makes that count zero, while unavailable data counts remain unknown.
- Final job results and a separate data-status column distinguish execution/delivery failures, partial data, and successful extraction. Sanitized `reports/automation/*_run.json` files back the summaries; they contain counts and output paths, not response payloads or credentials.

The usage adapter redacts configured secrets and embedded JWTs from legacy output and suppresses generic exception/response details. Leverage retains its existing sanitized errors and GET-only client. No logs contain authorization headers, passwords, raw dashboard responses, or new client payload dumps.

## Artifacts and confidentiality

Usage still emails `Weekly_Phrase_Usage_Reports.zip` and `Weekly_Phrase_SUSE_Cloud-Native_Usage_Reports.zip`; their attachment selection and complete mail configuration are preserved. It does not add a usage artifact.

When allowed, leverage uploads **Phrase-Leverage-<run-id>-<attempt>**, retained for 14 days. It contains the detailed, project, client and validation CSVs; structured master JSON; manifest/checksums; coverage CSV; and sanitized automation diagnostic. It excludes redundant raw JSONL snapshots and XLSX files from the artifact bundle. All outputs still exist in the leverage job's workspace during execution. Artifact access follows repository/run access; public repositories default to no upload. Unknown repository visibility also defaults to no upload. `PHRASE_LEVERAGE_ARTIFACTS=false` disables upload even in private repositories.

No extra storage service, data commit, mail recipient, or publishing action is introduced. All workflows retain `contents: read`; no write or OIDC permissions are granted. Existing checkout/setup-python/mailer/artifact actions remain pinned to full commit SHAs. No additional third-party action is added. The checkout token is not persisted into application Git configuration.

## Validation and development

```console
python -m pip install -r requirements-dev.txt
python -m unittest discover -v
```

The suite parses every workflow YAML, checks the single unchanged cron, mode defaults, job isolation, least privilege, action pinning, timeouts, reusable inputs, archive names, and original complete SMTP configuration checksums. Mocked execution tests run the original AI writer through the adapter and compare workbook values and file contracts. They also cover missing credentials, date defaults, artifact policy, telemetry, safe failures, and token/response redaction. No tests trigger a GitHub workflow or send email.

The workflows were additionally checked with checksum-verified **actionlint v1.7.12**, which validates Actions syntax, expressions, and reusable-workflow wiring. Runtime library names are unchanged; `requirements.txt` now includes patched-version minimums and `constraints-security.txt` following the vulnerability review. PyYAML 6.0.3 is pinned only in `requirements-dev.txt`. The Python 3.10 CI job installs that development file. Local tests use the available Python 3.13 interpreter plus Python 3.10 syntax checks; a live GitHub run and credentialed leverage validation are separate from mocked validation. See [LEVERAGE_VALIDATION_REPORT.md](LEVERAGE_VALIDATION_REPORT.md) for the 147-test result, security findings, and required real-data reconciliation.
