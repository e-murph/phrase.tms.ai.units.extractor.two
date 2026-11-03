# Read-only leverage extraction

Detailed/client-facing CSV reports and structured JSON are documented in [LEVERAGE_REPORTS.md](LEVERAGE_REPORTS.md), including validation, strict versus known-subset totals, decimal precision, and workflow scopes. They are generated automatically by the same entry point.

`python main.py` and the AI workbook outputs are unchanged. The original scheduled workflow now supports isolated usage/leverage/both jobs, preserving its cron and default usage mode; see [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md). Leverage also runs independently with `python leverage_main.py`. It uses the existing `requests`, `openpyxl`, optional dotenv convention, and product mappings.

## Credentials and GitHub Actions

The existing `PHRASE_API_URL`, `PHRASE_API_URL_WORDS`, `PHRASE_REFERER`, and `PHRASE_REFERER_WORDS` secrets contain embedded-dashboard URLs. `EMAIL_USERNAME` and `EMAIL_PASSWORD` are SMTP credentials. **None of these six values is a configured TMS REST credential.** Dashboard JWTs are not sent to the REST API, and email credentials must never be used for TMS authentication.

The extractor accepts an already available TMS `ApiToken` or Platform `Bearer` access token. It does not create credentials, exchange/refresh tokens, log in with a password, modify secrets, or change account permissions. An expired token fails with an actionable 401 diagnostic. A valid REST credential must be securely available before live leverage retrieval can run; the six existing dashboard/email secrets alone cannot satisfy that prerequisite.

For local use, place the existing REST token or full `ApiToken ...` / `Bearer ...` header in a protected environment variable or ignored `.env`. The default variable name is `PHRASE_TMS_AUTHORIZATION`. If a REST credential is already available under another environment name, use `--credential-env EXISTING_VARIABLE_NAME`. This argument contains the **variable name only**, never a token. `--auth-scheme Bearer` selects Bearer for an unprefixed token; a full prefixed header retains its own type. No credentials belong in command arguments or committed configuration.

The manual **Phrase Leverage Extractor** compatibility workflow calls the shared **Phrase AI Scraper** workflow with `mode=leverage`. Both use the same REST secret lookup and safe execution path. The shared workflow also supports scheduled leverage through a repository mode variable, without another cron. CSV/JSON reports and diagnostics upload for 14 days when confidentiality policy allows; they are never added to usage emails. See [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md) for all inputs/defaults. No secrets are created or changed; with only the six known dashboard/email secrets, REST access remains unavailable. Offline tests use no secrets.

## Running and scope

Install `requirements.txt` (including its security constraints) in a clean environment, then run with the credential securely configured:

```console
python leverage_main.py --period thisyear
python leverage_main.py --period all --project-uid PROJECT_UID
python leverage_main.py --start 2026-01-01 --end 2026-11-01 --client-id 123
python leverage_main.py --project-name "Exact project name" --client-name "Exact client name"
python leverage_main.py --credential-env EXISTING_VARIABLE_NAME --auth-scheme Bearer --base-url https://us.cloud.memsource.com/web
python leverage_main.py --help
```

Project UID/name and client ID/name options can be repeated. Names match exactly; numeric client IDs are not client UIDs. Different filter kinds intersect. Hidden client metadata is reported and cannot silently bypass a client filter. Project names enrich products using `mapping_settings.json`; mapping does not restrict scope or replace API language metadata.

The default period is January 1 of the current UTC year to the frozen run time. `--period all` removes the lower boundary; the upper boundary remains the run time. Explicit `--start` and `--end` override the respective boundaries. Dates mean UTC midnight; timestamps must include a timezone. The interval is start-inclusive/end-exclusive and filters **analysis creation**, not project/job creation or translation throughput. Discovery still inspects older projects and retains metadata for out-of-period analyses. Their category/job tables are not extracted for the filtered report. AI dashboard date definitions remain independent.

All configured workflow levels are inventoried. If no workflow steps are returned, the documented default job level 1 is queried. Archived projects are included in discovery by default; archived/unavailable resource reads can fail with 404 and are reported without restoration. `--exclude-archived` omits archived projects from discovery. `--only-owner-org` limits project analysis discovery to organization-owned analyses; reverse job discovery cannot reintroduce excluded analyses. Active jobs and all jobs referenced by existing analyses are retrieved. A separate full recycle-bin history is not reconstructed.

`--requests-per-second` defaults to 2, `--retries` to 3 additional attempts, and pages contain up to 50 records. Retriable connection failures, 429, and transient 5xx responses use bounded exponential backoff/jitter, `Retry-After`, and longer concurrency-limit waits. A requested wait over five minutes produces a retry-later error rather than ignoring the server or waiting indefinitely. Permanent authorization/not-found errors are not retried. Redirects and non-Phrase hosts are rejected. Analysis/download content is fetched with GET only; no authentication POST or content mutation exists in this implementation.

Exit code 0 means required discovery/count retrieval and applicable validation completed. It does **not** mean every job has an analysis or every optional capability is available: inspect the manifest and coverage. Export status is `partial` whenever client reporting is incomplete, even if retrieval exits 0. Exit 2 means a configuration, required retrieval, validation, or export failure. Optional download denials and inaccessible scheme metadata preserve available raw/net data and have separate completeness flags. Output errors leave a `writing` manifest rather than a stale success when data files were already being replaced.

## Outputs and repeated runs

The default output directory is `reports/leverage/`, outside the AI email ZIP globs. `--output` chooses a different dedicated directory. Each execution replaces the current files; it never appends business records. Stable IDs and keyed discovery remove duplicates within and across repeated snapshots. Keep separate run archives deliberately if history is required; do not concatenate snapshot files and then sum them as transactions. Files are replaced atomically individually, with a `writing` manifest first and the final manifest last. Concurrent writers to the same directory are unsupported; use separate output directories for concurrent local runs. Actions runs have isolated workspaces.

| Output | Contents |
| --- | --- |
| `Leverage_Statistics.xlsx` | Selected jobs, selection decisions, per-analysis language totals, job analysis inventory, original category measures, metadata, rates, coverage, and manifest sheets. No misleading grand total across overlapping grains/workflows/types. |
| `*.jsonl` | One record per line for projects, jobs, analyses, language totals, job analyses, selected jobs, selection decisions, measures, schemes, and coverage. Nested metadata and nulls are preserved. |
| `*.csv` | The same normalized tables for downstream tools. Empty cells are unknown/not available; a returned numeric zero stays zero. Nested fields contain JSON text. |
| `snapshots.jsonl` | Sanitized complete successful REST resource/page responses, plus native JSON downloads. Includes kind, key, endpoint version, and sanitized-payload SHA-256. Unknown fields/categories remain accessible. Identity responses are not archived because they can contain CSRF tokens/personal data. |
| `manifest.json` | Effective scope/period/policy, organization UID, request/record counts, required/metadata/optional completeness, timestamps, and limitations. |

Spreadsheet text is neutralized against formula injection; JSON retains original text apart from credential redaction. Excel splits large tables at its row limit and can truncate exceptionally long cell text; JSONL is authoritative. Raw and discounted values have separate fields. All generated outputs and Python caches are ignored by Git. Reports can contain confidential project/client/filename metadata; limit artifact access and archive retention accordingly.

## Selection and double-counting rules

All retrieved analysis types remain in the inventory. The default summary considers `PreAnalyse` with source-unit counts. Use repeated `--summary-type` for explicit additional types and `--count-basis target` for target-unit reports. Each analysis type has its own selection group; post-editing, compare, provider, and continuous analyses are never silently relabeled as default analyses.

For each analyzed job and analysis type, consider candidates inside the creation-date window with the requested count basis (or an unknown basis). Choose the latest creation timestamp, with analysis UID as a deterministic tie-break. Select only when metadata is accessible, count basis is verified, import status is `OK`, `outdated` is explicitly false, raw language/job availability is true, and exactly one corresponding job-count row exists. Estimates are excluded from the selected view. A newer inaccessible, outdated, running, unknown-date, or otherwise unusable candidate is **not replaced with an older analysis**. The reason is exported. Unknown type/reference metadata blocks an unsafe selection.

Selection is per analyzed job/workflow part. The same source file in translation and revision is two separate workflow records: choose the intended workflow before totaling. Split jobs retain part UID/source reference/bounds where available. Proven overlapping source segment ranges within one workflow/language/type are excluded from selection and flagged, preventing parent/child double counts. Unknown ranges cannot establish whether parts overlap. Duplicate filenames and project names are not identity. Analysis-language totals, all analysis/job inventory rows, and selected jobs overlap and must not be added together. A newer analysis covering only one job does not supersede old analyses for unrelated jobs.

The embedded v3 jobs array is capped at 100. The extractor always follows the paginated v1 membership endpoint and verifies pages/counts. Project and reverse job analysis discovery are combined and deduplicated. Raw/net job word totals are reconciled with language totals where both are available; inconsistent observations remain visible and mark required coverage incomplete rather than being silently adjusted. No missing analysis, missing category, or missing net scheme is converted to zero.

## Direct Phrase fields versus local calculations

| Export field/content | Origin |
| --- | --- |
| Project/client/analysis/job IDs, names, filenames, type, provider, settings, warnings, `outdated`, import status, languages, workflow references, associated scheme | Returned by Phrase. Metadata can be hidden by account permissions; absent fields stay null. Workflow level can also come from the explicit job-list level being queried. |
| `analysis_created_at` | Phrase `dateCreated`. |
| `analysis_updated_at` | Only an actually returned `dateUpdated`, `dateLastUpdated`, or `lastModifiedDate`; source field is recorded. The documented v3 response does not guarantee an update date. Creation/extraction time is never substituted. |
| `gross_words`, raw characters/segments | `data.all.words`, `.characters`, `.segments`, before weighting, within that analysis's settings. `gross_words` can describe source or target units; `count_basis` disambiguates. |
| `total_source_words` | Alias of returned gross words **only when** `settings.countSourceUnits` is explicitly true; otherwise null. |
| `discounted_words` and discounted characters/segments | Returned `discountedData.all` values, when an associated scheme and available net data are present. Not locally reconstructed from current rates. |
| Raw/discounted category measures | Exact numeric leaf fields under `data` / `discountedData`, including `all`, TM/MT/NT bands, repetitions, internal fuzzies, extra units, and unknown future categories. `field_path`, original band, endpoint version, and grain are retained. Numeric observations can still be present when availability is false; flags must be respected. |
| Availability/estimate flags | Returned flags. v3 language estimates are carried alongside job records; the v1 job schema does not guarantee its own estimate flag. |
| Net-rate scheme/workflow rates | Existing analysis association plus read-only v2 scheme/v1 workflow-rate metadata. Denied rate access does not discard Phrase-returned net counts. |
| `word_savings` | Local subtraction `gross_words - discounted_words`, only for available, compatible returned counts with an associated scheme. Negative values are retained and flagged. No scheme means unknown savings, not zero or 100%. |
| `word_savings_percent` | Local `100 * word_savings / gross_words`, only when gross words are positive. Zero/unknown denominators stay null. Do not sum/average percentages. |
| `count_basis`, settings fingerprint, period inclusion, product mapping, record IDs, selection, coverage, extraction timestamps/checksums | Local interpretation, enrichment, or processing provenance. |

Raw words are not guaranteed to cover every segment: settings can exclude locked/confirmed segments, numbers, MT/NT, or internal fuzzies. Preserve settings when comparing results. Analysis words, discounted work, AI billing units, processed words, and MT characters are distinct measures.

## Verified endpoint contracts and limits

Reverified on 3 November 2026 against the [official current OpenAPI YAML](https://developers.phrase.com/public/assets/openapi/phrase-tms-latest.yaml), downloaded-byte SHA-256 `115072e2b9511076108be298d597952241767e572bcdd6d57e1b597dbd7d4ca5`. Earlier fingerprints refer to separately serialized JSON/text versions, not these YAML bytes.

- [GET v3 analysis detail](https://developers.phrase.com/en/api/tms/latest/analysis/get-analysis): `/api2/v3/analyses/{analyseUid}`, no query arguments. Returns per-language `data` and `discountedData` plus metadata/settings.
- [GET native download](https://developers.phrase.com/en/api/tms/latest/analysis/download-analysis): `/api2/v1/analyses/{analyseUid}/download?format=JSON`. JSON is a file attachment and has a different shape from v3 detail. Downloads require their own permission.
- [Analysis guide](https://developers.phrase.com/en/guides/analysis): native JSON has per-source-level breakdowns, but attached schemes can make exported counts weighted. CSV/LOG flatten source categories. Native downloads are archived separately and never joined by ambiguous filenames or treated as the raw/net REST pair. `--no-download-json` skips this optional supplement.
- [Project analysis discovery](https://developers.phrase.com/en/api/tms/latest/project/list-analyses-by-project) and [job analysis discovery](https://developers.phrase.com/en/api/tms/latest/analysis/list-analyses) use v3. [Job membership/counts](https://developers.phrase.com/en/api/tms/latest/analysis/list-jobs-of-analyses) uses v1 with zero-based pagination, maximum page size 50.
- [Job listing](https://developers.phrase.com/en/api/tms/latest/job/list-jobs) uses v2 with a one-based `workflowLevel` default of 1; `count=true` is explicitly requested for verified pagination counts. Project/job metadata and identity use v1. [Current net-rate detail](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/get-net-rate-scheme.md) uses v2; workflow rates use v1.

**Known schema limitation:** v3 language NT bands differ from v1 job NT99/100 bands. These remain distinguishable; no NT99-to-NT95 conversion is invented. The richer native JSON is available in snapshots but cannot be safely joined to job UIDs by duplicate filenames. Full v3 language counts and all v1 job counts are retained, with the detail limit explicit in each job row and the manifest. A lossless v3-equivalent per-job category report cannot be promised by the documented v1 schema. Deleted/inaccessible records, analyses that never existed, and immutable historical states cannot be reconstructed. The extractor never creates or recalculates to fill such gaps.

## Tests

Install `requirements-dev.txt`, then run `python -m unittest discover -v`. All fixtures are synthetic; tests mock HTTP and require no live credentials or Phrase mutations. Coverage includes extraction, reporting, automation mode routing, workflow YAML, safe exports, and the original AI workbook contract. CI runs under Python 3.10. Live account validation remains pending a valid REST credential.

The initial extractor baseline on 3 November 2026 passed 58 tests under Python 3.13.5; the validation review now passes 147. The original AI source, mappings, and cron remain unchanged. The review adds security minimums for the same runtime libraries and existing HTTP dependencies; install the requirements rather than relying on older global packages. Python 3.10 CI is configured but was not executed from this workspace. The collector holds a run in memory; use bounded project/date scopes for very large organizations. [LEVERAGE_VALIDATION_REPORT.md](LEVERAGE_VALIDATION_REPORT.md) records fixes, exact test evidence, limitations, and the required two-project/two-language live reconciliation procedure.
