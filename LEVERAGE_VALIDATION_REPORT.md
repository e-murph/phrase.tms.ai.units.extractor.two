# Phrase TMS application validation report

Review date: **3 November 2026**. Evidence: source review, current official documentation/OpenAPI, synthetic HTTP fixtures, real CSV/JSON/XLSX writers, workflow linting, and a dependency vulnerability query. No production Phrase API calls, analysis mutations, UI automation, GitHub workflow dispatches, or email sends were performed.

**Readiness: verified for offline regression and synthetic accuracy testing; ready for a bounded live validation run after REST authorization is configured. Leverage is not yet approved as reconciled client/pricing data.** The six identified dashboard/email secrets do not authorize the REST API. No claim of successful live authentication, account-wide completeness, UI reconciliation, or production scheduling is made.

## 1. Implemented functionality

The original `main.py` retrieves embedded-dashboard AI units and subscription wordcounts, maps project names to products/languages, and writes the existing XLSX files. Its source and mappings remain unchanged. The automation adapter preserves its `date=thisyear` request, workbook values/columns, filenames, ZIP membership, and complete email configuration. It adds sanitized logs and failure detection. Legacy wordcount failures still produce the original zero fallback, explicitly reported as partial by automation; those zeros are not evidence of zero real word usage.

The independent leverage application discovers accessible projects, workflows, jobs, and existing analyses using GET only. It retains language-level v3 results, paginated v1 job results, raw `data`, separate `discountedData`, metadata, scheme references/rates, and optional native JSON attachments. It never creates/recalculates analyses or changes projects, jobs, rates, or memories.

Reports include detailed category CSV, project and client summary CSV, validation CSV, structured master JSON, normalized CSV/JSONL tables, and an inventory workbook. Validated summaries select the latest compatible in-period analysis per job/type, separate workflow/type scopes, and exclude ambiguous or invalid observations. Raw observations remain inspectable. Default scope: source-unit `PreAnalyse`, workflow 1, analysis creation from January 1 UTC through the frozen run time, start inclusive/end exclusive.

One existing Sunday 16:00 UTC workflow supports `usage`, `leverage`, and `both`; usage remains the default. Process jobs are independent. The old manual leverage entry calls the shared workflow. CSV/JSON artifacts have a 14-day retention and confidentiality gate; usage retains its existing ZIP emails. Generated client data is not committed.

## 2. Files changed

These changes were made during this validation review, on top of the existing enhancement work:

| Files | Review change |
| --- | --- |
| `leverage/client.py` | Reject nonstandard JSON NaN/Infinity safely at the HTTP boundary. |
| `leverage/normalize.py` | Suppress locally calculated savings for estimated raw/discounted counts, including inherited language estimates. |
| `leverage/extractor.py` | Validate job UID/project/workflow attribution, nested readiness/language/lineage metadata, and duplicate part IDs; preserve unrelated extraction after malformed responses; explicitly request job inventory counts; diagnose known split jobs without usable bounds. |
| `leverage/validation.py` | Reconcile individual job categories to language categories as well as grand totals; treat explicit estimates as unavailable for validated counts. |
| `leverage/reporting.py` | Enforce attribution/estimate results, expose estimate flags/status, and version reporting scopes as 2 for the stronger validation policy. |
| `leverage/export.py` | Do not label incomplete client reporting complete; require core completeness for `client_reporting_complete`. |
| `requirements.txt`, `constraints-security.txt` | Add minimum patched requests/HTTP dependency versions without adding a new runtime library. |
| `tests/test_validation_review.py` | New hostile-contract, estimation, category reconciliation, and reliability regressions. |
| `tests/test_golden_accuracy.py`, `tests/fixtures/leverage_golden.json` | New independent, hand-calculated two-project/two-language pricing fixture and end-to-end export assertions. |
| `tests/test_extractor.py` | Correct split/adjacent fixture workflow metadata so it tests segment overlap rather than a newly rejected workflow contradiction. |
| `tests/test_export_and_ai.py` | Assert that the fixture's missing analyses/net data produces partial reporting despite complete discovery. |
| `LEVERAGE_VALIDATION_REPORT.md`, `LEVERAGE_REPORTS.md`, `LEVERAGE_USAGE.md`, `GITHUB_ACTIONS.md`, `README.MD` | Review evidence, corrections, dependency instructions, and live reconciliation procedure. |

The enhancement's other principal files are `automation.py`, `leverage_main.py`, `leverage/config.py`, `leverage/numeric.py`, the three workflows, and the remaining tests. Every application module, test module, workflow, dependency/configuration file, and reporting document was inspected. `main.py`, `mapping_settings.json`, workflow YAML, and `.github/dependabot.yml` were not edited during this review. Existing uncommitted enhancement work was retained.

## 3. Official API endpoints and contracts

Rechecked the [official analysis reference](https://developers.phrase.com/en/api/tms/latest/analysis/get-analysis), [analysis guide](https://developers.phrase.com/en/guides/analysis), and [current OpenAPI YAML](https://developers.phrase.com/public/assets/openapi/phrase-tms-latest.yaml). Downloaded YAML SHA-256: `115072e2b9511076108be298d597952241767e572bcdd6d57e1b597dbd7d4ca5`. The guide/schema were fetched as public documentation without application credentials. All endpoints below use GET with the official regional HTTPS base URL ending in `/web`.

| Endpoint | Purpose |
| --- | --- |
| `/api2/v1/auth/whoAmI` | Check REST identity/organization; full personal/token response is not archived. |
| `/api2/v1/projects` | Paginated project discovery; client ID, archive, sort/order filters. |
| `/api2/v1/projects/{projectUid}` | Project/client/language metadata. |
| `/api2/v2/projects/{projectUid}/workflowSteps` | Configured workflow inventory. |
| `/api2/v2/projects/{projectUid}/jobs` | Paginated job inventory per `workflowLevel`, now explicitly `count=true`. |
| `/api2/v1/projects/{projectUid}/jobs/{jobUid}` | Job UID/project, filename, language, workflow, lineage and segment bounds. |
| `/api2/v3/projects/{projectUid}/analyses` | Paginated existing project analyses; `sort=DATE_CREATED`, `order=desc`, `onlyOwnerOrg`. |
| `/api2/v3/projects/{projectUid}/jobs/{jobUid}/analyses` | Reverse discovery/audit of existing analyses for a job. |
| `/api2/v3/analyses/{analyseUid}` | Metadata and language-part raw/discounted results; no query parameters. |
| `/api2/v1/analyses/{analyseUid}/analyseLanguageParts/{partId}/jobs` | All job counts/memberships, beyond the embedded 100-job reference limit. |
| `/api2/v1/analyses/{analyseUid}/download?format=JSON` | Optional attachment with its own export schema and potentially weighted counts. |
| `/api2/v2/netRateSchemes/{schemeUid}` | Current scheme metadata. |
| `/api2/v1/netRateSchemes/{schemeUid}/workflowStepNetSchemes` | Paginated workflow rate references. |
| `/api2/v1/netRateSchemes/{schemeUid}/workflowStepNetSchemes/{id}` | Current workflow rate detail. |

Pages are zero-based, maximum requested size 50. Discovery verifies stable pagination totals, duplicate identities, truncation and bounded traversal. Authorization supports TMS ApiToken and Platform Bearer headers, with no password login/token refresh. [Authentication reference](https://developers.phrase.com/en/api/tms/latest/authentication).

v1 job NT bands and v3 language NT bands differ. Both are preserved; validation compares their **NT origin totals**, not an invented band conversion. Other shared category bands are checked individually. Native downloads remain separate and never supply a second additive volume or a filename-based job join. [Job count contract](https://developers.phrase.com/en/api/tms/latest/analysis/list-jobs-of-analyses), [download contract](https://developers.phrase.com/en/api/tms/latest/analysis/download-analysis).

## 4. Test results

Baseline: **126 tests passed** before review changes. Final: **147 tests passed**, including all existing tests plus 21 new test methods with multiple subcases. The final complete run used Python **3.13.5** and an isolated installation satisfying the new security floors: 147 tests in **29.062 seconds**, exit 0. No network calls to Phrase or emails are used by tests.

| Module | Tests | Main evidence |
| --- | ---: | --- |
| `test_automation.py` | 28 | All modes, frozen dates/configuration, isolated failures, missing credentials, stale manifests, safe logs, final outcomes. |
| `test_client.py` | 17 | Auth/configuration, GET-only paths, transport/retries, pagination, redaction. |
| `test_export_and_ai.py` | 7 | Actual legacy workbook writer, original columns/totals, export replacement, spreadsheet injection protection for leverage, CLI failures. |
| `test_extractor.py` | 34 | Multi-job/language/workflow/analysis discovery, filters, selection, ownership, availability, estimates, partial failures, split overlaps. |
| `test_golden_accuracy.py` | 3 | Independent expected project/client/category totals and real CSV/JSON agreement. |
| `test_reporting.py` | 30 | Precision, missing values, category/control validation, raw/net separation, aggregate identity and strict coverage. |
| `test_validation_review.py` | 18 | Confirmed review defects, malformed metadata, duplicate parts, estimated savings, all retriable HTTP statuses, nonfinite JSON, explicit count discovery. |
| `test_workflows.py` | 10 | All YAML files, sole unchanged cron, dispatch/reuse wiring, job isolation, permissions, SHA pins, complete original SMTP configuration checksums and artifacts. |

Additional checks: **actionlint v1.7.12 passed all three workflows** (external shellcheck/pyflakes integrations disabled); PyYAML parsing passed; Python 3.10 syntax parsing passed for application/test Python files; `git diff --check` passed. Original AI source and mappings match Git HEAD with normalized line endings. A disclosure scan found none of the configured `.env` values in source, tests, workflows or documentation. No credential values are recorded here.

Only Python 3.13 is installed locally. Native Python 3.10/Linux execution, hosted action runtime/SMTP delivery, GitHub artifact access, and real schedules were **not executed**. Syntax/static wiring tests are not substitutes for those live checks.

### Scenario coverage

| Requested scenario | Verified behavior with mocks |
| --- | --- |
| One/multiple jobs; multilingual projects | Literal fixture verifies 5 jobs in 2 projects and 2 target languages; no parent-total duplication. |
| Multiple workflows | Inventory across levels; reporting scopes remain separate. |
| Multiple analyses | Latest compatible analysis per job/type, deterministic timestamp tie-break, no older fallback for an unusable latest candidate. |
| No analyses/empty responses | Explicit coverage gaps, null unavailable volume, genuine empty inventories, stable headers. |
| With/without schemes | Returned raw/net pairs preserved; missing schemes do not manufacture net words/savings. Denied scheme metadata does not replace returned counts. |
| Repetitions, internal fuzzies, TM/MT/NT | Actual category keys retained, 101/100 separate, controls and category distributions checked. |
| Partial API failures | Unrelated jobs/projects continue; required failures produce partial output and exit 2. Optional failures have separate completeness indicators. |
| Pagination | 103-record lists and analysis membership beyond embedded 100; changing/truncated/duplicate pages fail explicitly. |
| 401/403/404 | No retry for permanent failures; 401 stops further credential use/discovery; no response payload leakage or zero invention. |
| 429/500/502/503/504 | Bounded retries, pacing/backoff, Retry-After and retry-later behavior. |
| Malformed/incomplete data | Count/type/language/status/identity checks; safe NaN/Infinity rejection; unaffected results preserved. |

## 5. Reporting accuracy findings and fixes

Successful HTTP responses alone did not satisfy accuracy requirements. Review regressions reproduced these defects before the fixes:

| Finding | Before | Corrected behavior |
| --- | --- | --- |
| Estimated discounted counts | An explicit language `discountedData.estimate=true` still yielded complete net/savings. | Estimated observations are preserved/labeled; validated net, savings and percentages are null. Estimated raw detail cannot claim validated savings either. |
| Compensating category errors | Moving volume from repetition to TM could leave every `all` total unchanged and pass validation. | Reconcile per-band job sums against language categories for words, characters and segments; NT uses origin totals across API versions. |
| Job identity/workflow mismatch | Wrong returned UID/project or contradictory workflow metadata could be counted. | Explicit attribution diagnostics and exclusion; unrelated language results remain available. |
| Malformed nested fields | Lists/objects in readiness, language, project or lineage fields could abort the run. | Validate nested contracts, avoid guessed dimensions, retain originals in source snapshots, continue unaffected records. |
| Duplicate language-part ID | Duplicate parts could overwrite normalized inventory without an explicit duplicate diagnosis. | Retain original parts, diagnose ambiguity, exclude affected reporting. |
| Empty/malformed identities | Empty client IDs could combine unrelated projects, and malformed scheme IDs could be accepted as a discount association. | Keep unresolved clients separate by project; only syntactically valid scheme identities can establish validated discounted data. |
| Known split without bounds | Missing lineage/ranges could bypass proven-overlap protection. | Flag unverified split coverage and exclude it from validated reporting. |
| NaN/Infinity | Python accepted invalid JSON constants; later snapshot/export could fail unexpectedly. | Reject immediately with sanitized INVALID_JSON diagnostics. |
| Reporting completeness label | Complete retrieval could produce `status=complete` despite missing client reporting populations/net data. | Export status is partial whenever client reporting is incomplete; core completeness remains separately visible. |

Job listing now requests its documented `count` parameter explicitly because the client relies on count metadata. No assertion is made that omitted counts caused missing live records; that behavior requires live verification.

### Independent arithmetic check

The committed golden fixture uses hand-entered source-category volumes and expected totals, independent of production aggregation code. For a 1,000-word job: TM101 100; TM100 200; TM95 150; TM0 100; MT95 100; NT 50; internal fuzzy 100; repetitions 200. Returned discounted categories total **335** words; locally calculated savings are **665**, or **66.5% of gross words**. These are synthetic examples, not account results.

| Scope | Gross | Discounted | Saved | Covered jobs |
| --- | ---: | ---: | ---: | ---: |
| Project P1, en→de | 1500 | 502.5 | 997.5 | 2 |
| Project P1, en→fr | 700 | 234.5 | 465.5 | 1 |
| Project P2, en→de | 1000 | 335 | 665 | 1 |
| Project P2, en→fr | 2000 | 670 | 1330 | 1 |
| Client, en→de | 2500 | 837.5 | 1662.5 | 3 |
| Client, en→fr | 2700 | 904.5 | 1795.5 | 2 |

The tests compare individual category volumes, all three count units, raw/net separation, selected detail sums, client aggregation and the written CSV/JSON against these expectations.

### Field provenance and calculation rules

Names/UIDs, languages, workflow, analysis type/date/settings, available/estimate flags, scheme references and each raw/discounted category/control count are **returned by Phrase**. Update timestamps remain null unless returned; creation time is not substituted. Scheme detail represents the current API snapshot, not a reconstructed historical rate.

Stable record/scope IDs, selection decisions, validation status, sums, differences and percentages are **calculated locally**. Saved words = gross minus compatible, available, validated discounted words. Leverage = `100 × saved / gross` for the same complete population. A zero/unknown gross denominator yields null percentage. Partial paired subsets have explicitly labeled known totals and their own paired gross denominator; percentages are volume-weighted, never averaged per job.

`Decimal` parsing/exact sums preserve original numeric precision; CSV/JSON are authoritative over Excel display precision. Raw and discounted fields never share one value column. Missing observations remain null/blank, genuine returned zero remains numeric zero, and missing categories are not fabricated. An origin/band missing from one constituent can therefore make its full aggregate unknown while a known subtotal remains available. `tm_words` includes all returned TM bands, including `match0`; use bands rather than the origin subtotal to price usable matches.

Control totals are not detail categories. Analysis/language inventory is not added to selected job totals. Default, post-editing and other types/workflows stay distinguishable; incompatible count bases and known overlaps are excluded. Reporting scope version 2 distinguishes the stronger review policy from earlier snapshots.

## 6. Known limitations

- Live access/figures remain unverified. The available dashboard/SMTP secret types cannot authenticate the TMS REST API; there is no password login or token refresh fallback.
- v1 cannot promise the same per-job NT band richness as v3 language data. Preserve both; do not rename NT99 to NT95 or prorate language totals to jobs.
- Periods describe analysis creation, not translated-in-period throughput. A later edited/recalculated analysis or changed scheme can alter a current observation under an existing UID. Historical state requires retained snapshots.
- Selection is latest compatible **inside the window**, whereas Analytics may use a different latest-analysis population. Shared ownership, refresh delay, deleted/inaccessible analyses, internal fuzzy settings, zero results and missing schemes also differ. Align those definitions before dashboard comparison. [Official Analytics definitions](https://support.phrase.com/hc/en-us/articles/6554447243036-Analytics-Dashboards-TMS).
- Known overlaps are excluded where lineage/ranges identify them; unidentified split relationships and concurrent account changes still require real-data checks. Pagination count checks cannot guarantee a transactional snapshot if counts stay unchanged while membership changes.
- In-memory snapshots and per-project/job API calls can be expensive for large organizations. Date filtering does not remove the need to discover old projects/analyses. Use bounded project/client scopes and verify the 60-minute hosted budget against real volume/rate limits.
- Files are atomic individually with a manifest written last, not a transactional directory swap. Do not read while status is `writing`, combine dated snapshots as transactions, or run local concurrent writers into one directory. Follow current manifest filenames/checksums.
- Public/unknown-visibility artifact upload defaults off; then leverage output exists only in the ephemeral hosted workspace unless another approved retention mechanism is selected. Even private artifacts contain confidential names, counts and rate metadata.
- Existing AI logic intentionally retains dashboard units, name-based joins/mapping and zero wordcount fallback. Actual dashboard row uniqueness, same-named projects, missing wordcounts and upstream totals still need live verification. Legacy AI workbook strings also retain their existing formula-interpretation behavior; leverage exports neutralize formula strings. This review did not silently change legacy workbook semantics.

## 7. Unverified assumptions and manual reconciliation

The schemas describe the intended contracts, not observed account responses. Live tests must establish role/region permissions, scheme/download access, job/workflow identity and split semantics, actual category partition/rounding behavior, raw/net estimate flags, count metadata with `count=true`, availability of provider/continuous/other analysis types, and compatibility with the account's real analysis settings. Do not infer account access from a 200 identity response alone.

### Required reconciliation sample

Choose **two genuine accessible projects, each with two target languages** (for example en→de and en→fr). Project A should have multiple jobs, repeated analyses and TM101/100/fuzzy, MT, NT and repetitions/internal fuzzies. Project B should exercise another workflow and include both a scheme-backed analysis and a missing-analysis or missing-scheme case where available. Verify the main workflow-1/source/PreAnalyse scope first; compare workflow 2 or post-editing only in separate scopes.

| Comparison | Actual project/analysis UIDs | Target | Raw categories/total | Discounted categories/total | Result |
| --- | --- | --- | --- | --- | --- |
| Project A, target 1 | Pending authorized selection | de (example) | Not run | Not run | **UNVERIFIED** |
| Project A, target 2 | Pending authorized selection | fr (example) | Not run | Not run | **UNVERIFIED** |
| Project B, target 1 | Pending authorized selection | de (example) | Not run | Not run | **UNVERIFIED** |
| Project B, target 2 | Pending authorized selection | fr (example) | Not run | Not run | **UNVERIFIED** |

1. With authorized API access and ordinary manual UI access, record organization/region, capture time, project/job/analysis UIDs, exact language pair, workflow, analysis type, source/target count basis, analysis date/status/outdated flag, settings and associated net-rate scheme. Do not create, recalculate, change schemes, edit jobs or restore records. Freeze a UTC creation-date interval containing the selected existing analyses.
2. Run leverage with both exact project UIDs, explicit start/end and workflow 1 into a dedicated directory. Keep the manifest, validation CSV, selection decisions, master JSON and report checksums as one confidential evidence bundle. Stop if `writing`, core failure or validation failure is reported; diagnose gaps before treating totals as complete.
3. Use selection decisions and `selected_for_summary` to identify the **exact selected analysis per job**. In Phrase open those analyses manually. One project can use job J1 from analysis A2 and J2 from A1: opening only the newest whole-project analysis is not an equivalent comparison. Never add superseded, parent language, or workflow-2 counts again.
4. For each of the four project/language cells, record UI raw total words, characters and segments, then every actual TM101, TM100, fuzzy/no-match band, MT, repetition, internal fuzzy and NT value. Compare job detail to its job CSV rows and language controls to the language part in master JSON. Sum only selected unique jobs for project totals. Check NT per-job bands against v1 values and NT language bands against v3; compare origin totals across versions without inventing a band mapping.
5. Compare UI **Net rate** figures and scheme identity to returned `discountedData`, separately from raw figures. A scheme-backed native JSON attachment can be weighted; it must not be treated as the raw report. Compare individual discounted categories and their total. Do not replace a missing scheme/discounted result with zero or locally apply current scheme percentages to manufacture a result. Explicit estimates fail this final-count comparison.
6. Recompute saved words by subtraction and percentage using the exported matching gross denominator. Compare project/category totals, covered/missing-job counts and client totals (sum project summaries once). Verify no post-editing/workflow mixing, duplicate business keys, null-to-zero conversion or same-filename job joining. For genuine zero gross, percentage must stay null.
7. Compare unrounded numeric observations with the configured absolute tolerance (default `0.000001`). If the UI rounds to two decimals, record that display resolution and obtain an authorized native download/API observation for precision. Do not increase tolerance or round the report simply to force agreement. Record every difference, setting/snapshot mismatch and inaccessible job explicitly.
8. Save a reconciliation table with UIDs/scope, metric/category, UI value and precision, API raw or discounted path/value, CSV/JSON value, difference, reason and reviewer/date. Repeat a bounded extraction with the **same explicit dates**; business keys and counts must be stable if Phrase data/settings have not changed. Sign off all four cells or document an accepted limitation. Store evidence privately; never attach credentials.

These steps are a procedure, **not a completed reconciliation**. The synthetic P1/P2 fixture is not a substitute for the four genuine comparisons.

## 8. Security findings

### Dependency vulnerabilities and remediation

OSV querybatch was used on exact package names/versions, not on repository payloads or credentials. The older local installation had advisories affecting four existing HTTP/trust dependencies; application exploitability was not assumed. Minimums now prevent those known affected versions from satisfying application installation:

| Package | Older local version | Enforced minimum | Advisory evidence |
| --- | --- | --- | --- |
| requests | 2.32.5 | 2.33.0 | [GHSA-gc5v-m9x4-r6x2](https://github.com/advisories/GHSA-gc5v-m9x4-r6x2), insecure temporary-file reuse. |
| idna | 3.11 | 3.15 | [GHSA-65pc-fj4g-8rjx](https://github.com/advisories/GHSA-65pc-fj4g-8rjx), input-related denial of service. |
| certifi | 2026.2.25 | 2026.10.18 | [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2), compromised-intermediate trust issue. |
| urllib3 | 2.6.3 | 2.8.0 | [GHSA-8988-9cw3-xx77](https://github.com/advisories/GHSA-8988-9cw3-xx77), plus GHSA-gh4c-6fx4-qh6g, GHSA-mf9v-mfxr-j63j, GHSA-qccp-gfcp-xxvc and GHSA-vxq7-64xx-v4gw. |

Final isolated tested set: requests 2.34.2, urllib3 2.8.0, certifi 2026.10.23, idna 3.20, charset-normalizer 3.5.2, pandas 3.1.0, numpy 2.5.4, openpyxl 3.1.5, et_xmlfile 2.0.0, python-dateutil 4.0.0, python-dotenv 1.2.4, tzdata 2026.6 and PyYAML 6.0.3. **OSV returned no known advisories for those 13 exact versions on the review date.** A separate Python-3.10-targeted dependency resolution (15 packages, including pandas 2.3.3/numpy 2.2.6) also returned no known advisories; resolution was performed from Windows and is not a Linux runtime test.

The shared/global Python installation was not upgraded. Install the constrained requirements in a clean environment before live use. This is a dated advisory check, not proof of absence of vulnerabilities. Requirements retain compatible upgrades rather than a full hash-locked environment; future resolution can differ. Dependabot currently monitors monthly action updates only, not pip requirements. Repeat dependency audits and consider a tested lock/constraints update process.

### Application and automation controls

- Leverage exposes GET only, validates regional HTTPS hosts/resource paths, disables redirects and does not disable TLS verification. Dashboard/email credentials are not repurposed as REST authorization.
- Credentials enter through environment/secrets, never command-line token arguments. Authorization is absent from public settings/repr. Logs use static diagnostics and redact configured values/JWTs; archived payloads are sanitized too. Full exception/response text is not echoed. Schema errors are actionable by code and scope, with confidential originals in local sanitized snapshots.
- All workflow permissions remain `contents: read`; checkout credentials are not persisted; external actions are pinned to full SHAs. User inputs pass through environment/configuration rather than shell interpolation. Offline push/PR tests use no secrets and do not run reporting/email jobs.
- Artifacts default to private repositories only and exclude redundant raw snapshot JSONL/XLSX; no generated data is committed or newly emailed. Master JSON still contains confidential project/client/job/rate metadata. Review repository audience and retention before enabling uploads in a public repository.
- Rate limiting defaults to 2 requests/second, three additional retries, bounded pagination and timeouts; 429/Retry-After is honored. A requested wait over five minutes produces retry-later failure. These controls are verified with mocks, not a guarantee against account-specific quotas. [Official rate-limit guidance](https://developers.phrase.com/en/api/tms/latest/handling-api-rate-limits).
- A single reporting concurrency group avoids overlapping active hosted runs; current/same-profile files are replaced, not appended. Rerunning usage can resend its normal email; stable report IDs do not provide exactly-once SMTP delivery. GitHub schedules can be delayed/dropped under load, and public inactive repositories can have schedules disabled; the existing top-of-hour cron is intentionally preserved. [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## 9. Outstanding issues and release gates

| Gate/issue | Status / action |
| --- | --- |
| REST credential and role access | **Blocked for live leverage** by the currently identified secret types. Securely configure a valid already authorized REST credential; do not paste it into chat/logs. Verify identity, projects, analysis, membership, scheme and download access separately. |
| Four genuine reconciliation cells | **Not run.** Complete section 7 and resolve/accept discrepancies before client/pricing reliance. |
| Hosted execution | **Not run.** Run the secret-free Python 3.10 CI first, then an authorized bounded leverage dispatch. Verify private artifact access/retention. Usage/both live runs send existing emails, so coordinate the actual delivery check. |
| Account-scale coverage and duration | **Unverified.** Measure discovery, pagination, quotas, split lineage and job counts against real representative projects before broad scheduling. |
| Historical comparisons | Retain approved immutable evidence outside ephemeral runners; compare compatible scope versions/settings and never sum snapshots. No new storage destination was invented. |
| Dependencies | Patched minimums and isolated tests verified; older shared installation remains untouched. Repeat audits, and establish pip update/lock management if reproducibility is required. |
| Legacy AI assumptions | Dashboard uniqueness/name joins, missing-wordcount zeros and spreadsheet formula-string semantics are retained for compatibility and require separate agreed hardening if relevant to actual data. |

No confirmed defect reproduced by the new leverage accuracy regressions remains unfixed. That statement is limited to the reviewed contracts and tests; it is not a live financial/reporting sign-off.

## 10. Running the application and checks

From the repository root, create/activate a clean Python 3.10+ virtual environment, then:

```console
python -m pip install --upgrade -r requirements-dev.txt
python -m unittest discover -v
```

Runtime-only installation uses `python -m pip install --upgrade -r requirements.txt`, which includes `constraints-security.txt`. A stale already-installed HTTP library can otherwise remain vulnerable; use the requirements rather than installing the four library names individually.

Usage remains:

```console
python main.py
```

With a valid REST header/token securely configured in `PHRASE_TMS_AUTHORIZATION` (or an existing environment variable selected by name), a bounded read-only example is:

```console
python leverage_main.py --project-uid ACTUAL_PROJECT_A_UID --project-uid ACTUAL_PROJECT_B_UID --start 2026-01-01 --end 2026-11-01 --report-workflow-level 1 --output reports/leverage-validation
python leverage_main.py --help
```

Use actual approved UIDs/dates; the command contains no credentials. Dates affect leverage only. Inspect `manifest.json`, its current report files/checksums, `report_validation`, `client_reporting_complete`, project coverage and validation CSV before consumption. `status=partial` can coexist with exit 0 when retrieval is complete but jobs/net data are unavailable; exit 2 denotes required retrieval/configuration/export/validation failure. A 200 response or exit 0 is not an accuracy certificate.

For GitHub Actions, choose **Phrase AI Scraper → Run workflow → usage/leverage/both**. Optional start/end are leverage-only, defaulting to current-year UTC through one frozen timestamp. `PHRASE_REPORT_MODE` configures scheduled mode without editing code; default `usage`, unchanged Sunday 16:00 UTC schedule. `PHRASE_TMS_CREDENTIAL_SECRET` chooses an existing REST secret by name. `PHRASE_LEVERAGE_ARTIFACTS` controls upload privacy. See [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md) for all variables, filters and failure behavior and [LEVERAGE_REPORTS.md](LEVERAGE_REPORTS.md) for schemas/calculations.

**Final assessment:** legacy usage behavior and enhanced mode/workflow contracts are regression-tested; leverage extraction, selection, validation, exact arithmetic and exports are verified with mocks and independent expected numbers. Real credential access, actual account schema/coverage, hosted execution, and all four UI reconciliations remain mandatory live checks before relying on leverage figures for client reporting or pricing.
