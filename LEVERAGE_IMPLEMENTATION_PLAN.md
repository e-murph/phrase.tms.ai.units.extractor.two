# Phrase TMS leverage extraction: review and implementation proposal

Reviewed: 3 November 2026. Status: **historical review and proposal**. The subsequent implementation is documented in [LEVERAGE_USAGE.md](LEVERAGE_USAGE.md); the sections below preserve the pre-implementation findings and recommendations. Live account validation still requires a valid TMS REST credential.

## 1. Findings and scope

The existing application retrieves AI units and wordcounts from **embedded Metabase analytics endpoints**, not from the public TMS project/job REST endpoints. Preserve that reporting path and its existing output contracts while adding an independent, read-only TMS analysis extractor.

Existing analyses can supply leverage statistics, but completeness is limited by the analyses already created, their settings, account visibility, and API response versions. Missing analyses or excluded categories cannot be recovered by reading another field. This proposal never creates, edits, restores, or recalculates Phrase content.

Key findings:

- The local configuration contains four embedded-dashboard URL/referer values. No separate TMS REST credential is configured in the inspected application or workflow. Dashboard JWTs are not evidence of TMS REST authorization.
- The current request period is `date=thisyear`; weekly describes delivery cadence, not a weekly reporting period.
- The only workflow runs Sundays at 16:00 UTC and by manual dispatch. It emails two ZIPs. It has **no artifact-upload step**, despite the README's artifact-download instructions.
- There is no existing test suite. Syntax/configuration checks passed, and five temporary offline characterization checks completed; several confirmed undesirable existing behavior rather than correctness.
- Comprehensive language-pair results are available through v3 analysis detail. Job results and membership use v1 endpoints with a narrower schema. Embedded analysis job arrays stop at 100 records; explicit pagination is necessary.

At the review stage, only this document was added. Application code, dependencies, mappings, workflow, schedule, credentials, and Phrase analyses were unchanged. No live Phrase account requests or emails were sent. Public official documentation was fetched, and credential values were neither printed nor copied here.

## 2. Repository and current architecture

The complete working-tree inventory was inspected before this document was created: seven tracked files, plus the ignored local `.env` inspected for key names and redacted credential structure. No applicable `AGENTS.md`, other application modules, tests, dependency lockfile, packaging configuration, or additional workflows were found. Git internals are not application source. The working tree was initially clean.

| File | Role and findings |
| --- | --- |
| `main.py` (258 lines) | Sole application module and executable entry point. Environment loading, HTTP, parsing, mapping, merging, sorting, and Excel exports are combined in one file. |
| `requirements.txt` | `requests`, `pandas`, `openpyxl`, `python-dotenv`; all unpinned. No test dependencies or Python package metadata. |
| `mapping_settings.json` | 24 product entries and 118 exact project-name mappings; no duplicate project-name keys across entries. Two entries share the same product label and therefore group together. |
| `README.MD` | Setup, dashboard-request capture, secret names, local execution, email delivery, manual dispatch, and JWT maintenance instructions. Artifact instructions do not match the workflow. Claims an Apache 2.0 license, but no separate LICENSE file is present. |
| `.github/workflows/scrape_stats.yml` | Sole workflow; scheduling, Python setup, scraper execution, ZIP creation, and SMTP email delivery. |
| `.github/dependabot.yml` | Monthly updates for GitHub Actions only; no pip dependency-update configuration. |
| `.gitignore` | Ignores `.env` only. Generated reports, ZIPs, caches, and future raw snapshots would otherwise be easy to commit accidentally. |
| `.env` (ignored, local) | Four dashboard configuration keys only. Values were examined structurally without disclosure. This local file does not prove what secret values exist in GitHub. |

Current flow:

```text
GitHub Actions / python main.py
  -> environment (optional local dotenv loading)
  -> GET AI dashboard + GET wordcount dashboard
  -> positional row parsing into pandas DataFrames
  -> outer merge on Project Name
  -> project-name product/language mapping, regex fallback, sorting
  -> product / language / unmapped / master XLSX files
  -> workflow ZIPs -> two SMTP email steps
```

### Entry points and reusable code

`main.py:257-258` calls `extract_ai_units()` when executed. Importing the module loads configuration and constructs headers but does not fetch data. `extract_ai_units()` at lines 97-255 performs the entire extraction/export process. `save_and_format_excel()` at lines 52-94 is the only separate helper.

The mapping logic at lines 191-219, grouping/sorting patterns at lines 221-250, and pandas/openpyxl dependencies are reusable concepts. There is **no existing REST client, paginator, authentication manager, retry helper, date-window utility, persistent store, or schema validator** to reuse. Reuse the exact mapping as product enrichment, not as record identity or the boundary of authorized projects. Actual source/target languages must come from REST metadata.

The Excel helper is reusable only after deliberate generalization: it totals only `AI Units` and `Wordcount`, assumes a `Project Name` label, uses a single `Data` sheet, and computes column letters with `chr(65 + idx)`, which breaks beyond column Z. It must not silently sum IDs, percentages, match scores, or repeated aggregate rows.

### Authentication and configuration today

At lines 9-22, optional `python-dotenv` loading precedes environment lookups. Missing dotenv is silently tolerated. GitHub injects secrets directly, so a local dotenv file is unnecessary in Actions.

| Variable | Purpose |
| --- | --- |
| `PHRASE_API_URL` | AI-units embedded dashboard request URL |
| `PHRASE_REFERER` | AI dashboard referer URL |
| `PHRASE_API_URL_WORDS` | Wordcount embedded dashboard request URL |
| `PHRASE_REFERER_WORDS` | Wordcount dashboard referer URL |

All four local values point to `analytics.cloud.memsource.com`, use embedded-dashboard routes, and contain JWT-shaped components. No URL path, signature, payload, or token is reproduced. The application passes these URLs with `referer`, Metabase embedding headers, JSON headers, language, and a browser user-agent (lines 24-45). It sends no `Authorization` header, does not log in to TMS, and implements no refresh or expiration handling. The README describes manually recapturing dashboard requests when tokens expire.

### Requests, pagination, filters, dates, and errors

- Two sequential `requests.get()` calls (lines 108 and 131), each with `timeout=30`; no shared session, retry/backoff, explicit throttling, or rate-limit-header handling.
- HTTP 200 and 202 are treated as data-bearing success. There is no polling for 202 responses, schema/column-metadata validation, `raise_for_status()`, or server-side completion check.
- No pagination, truncation detection, or count reconciliation. Only the returned `data.rows` array is consumed.
- Both requests add the literal query parameter `date=thisyear` (lines 47-49). Existing request URLs may also encode dashboard scope. The repository does not contain the dashboard definitions, so their actual SQL filters, date basis, and enforced scope cannot be established from source alone.
- The local AI referer has a `date=thisyear` hint. The wordcount referer additionally has `job_ownership=Own content` and `workflow_step_level=1`. These are **referer URL hints, not explicit extraction request parameters**; whether the dashboard backend applies the same restrictions needs response/dashboard validation. A referer is not a reliable filtering contract.
- No explicit project/client API filter, project UID, client ID, status filter, or start/end timestamp exists. Mapping is categorization after retrieval; unmapped projects are retained.
- The top-level language list contains six locales, but mappings also include `ko-KR`. The program does not use that list as a filter. Regex fallback recognizes only `aa-BB`-style locale strings in project names; it is not a complete locale parser.
- `datetime.now()` controls filenames using the execution host's local, naive clock. Normal outputs have date-only filenames, so same-day reruns overwrite the same names locally. Raw fallback filenames have minute precision. There is no explicit timezone, period metadata, run ID, watermark, or incremental window.
- AI non-success returns after printing a status. Wordcount HTTP failure falls back to zero wordcounts. Invalid wordcount numbers become zero, and short wordcount rows are silently skipped. A network/JSON exception in the wordcount request instead reaches the outer exception handler and can abort all exports.
- A broad `except Exception` prints the exception and returns. Most failures do not produce a nonzero exit status, so the scraper step can appear successful without complete reports. Exception strings can contain credential-bearing URLs.

### AI metrics, aggregation, and storage

AI rows are interpreted as `row[0] = Project Name`, `float(row[3]) = AI Units` (lines 114-123). Wordcount rows use `row[0]` and `row[4]`, removing commas before numeric conversion (lines 134-159). Other columns and source metadata are discarded. Neither metric's upstream aggregation definition can be verified without the dashboard definition or a real response.

The outer join uses project **names**, without uniqueness checks or preaggregation (lines 168-177). Duplicate names on both sides produce a many-to-many join and inflate values. Empty AI data creates a DataFrame without expected columns; a wordcount-only result can then fail during the merge. Nonempty inputs are mapped, sorted by trailing project number then language, and grouped for exports. Grouping creates report subsets; project rows are not otherwise consolidated. The helper sums metric columns for the final bold `TOTAL` row.

Results live in memory and then as root-directory XLSX files; there is no database, CSV/JSON export, raw-response archive, durable history, deduplication, or checkpoint. Every workbook contains one `Data` sheet with fitted widths; project/product columns are capped at width 100.

Output contracts to preserve:

- `Master_Phrase_Usage_Stats_YYYY-MM-DD.xlsx`
- `SUSE_Cloud-Native_Product_Usage_Stats_<product>_YYYY-MM-DD.xlsx`
- `SUSE_Cloud-Native_Language_Usage_Stats_<locale>_YYYY-MM-DD.xlsx`
- `Unmapped_Projects_Usage_Stats_YYYY-MM-DD.xlsx`, when needed
- `Raw_Phrase_Data_YYYY-MM-DD_HH-MM.xlsx`, when the mapping file is missing

The master/product/language columns are `Project Name`, `Product Name`, `Language`, `AI Units`, `Wordcount`. Unmapped outputs omit product. Product filenames replace spaces and slashes only. Every mapped product, including Edge and MLM products, receives the Cloud-Native prefix and matches the workflow's product ZIP glob; the prefix is not a client filter.

## 3. Existing GitHub Actions workflow

| Aspect | Current behavior |
| --- | --- |
| Name / job | `Phrase AI Scraper` / `build` |
| Schedule | `0 16 * * 0`: Sundays at 16:00 UTC; unchanged |
| Manual trigger | `workflow_dispatch`, no inputs |
| Other triggers | No push or pull-request trigger |
| Runner / Python | `ubuntu-22.04`, Python `3.10` |
| Permissions | `contents: read` |
| Setup | Checkout and setup-python actions pinned to full commit SHAs; upgrade pip; install unpinned requirements |
| Execution | `python main.py` from repository root |
| Scraper secrets | The four `PHRASE_*` variables above, scoped to the scraper step |
| Email secrets | `EMAIL_USERNAME`, `EMAIL_PASSWORD`, supplied as SMTP action inputs |
| Full ZIP | All root `*.xlsx` -> `Weekly_Phrase_Usage_Reports.zip` |
| Product ZIP | `SUSE_Cloud-Native_Product_*.xlsx` -> `Weekly_Phrase_SUSE_Cloud-Native_Usage_Reports.zip` |
| Delivery | Two SHA-pinned `dawidd6/action-send-mail` steps; Gmail SMTP, port 465, TLS; both currently target the same configured recipient |
| Artifacts / history | No `upload-artifact`, retention policy, report commit, external durable store, or cache |
| Validation | No tests, coverage checks, output-count assertion, or manifest |
| Failure / overlap | ZIP steps exit successfully when no matching files exist; email steps still reference the missing ZIP. No explicit concurrency group or job timeout is configured. Later delivery failures can prevent subsequent default-condition steps. |

No workflow was triggered during this review. Remote run history, actual GitHub secret values, recipient delivery, and organization settings were not inspected; source review is not proof of live execution health.

## 4. Official Phrase API investigation

The requested [TMS reference](https://developers.phrase.com/en/api/tms) and [analysis guide](https://developers.phrase.com/en/guides/analysis) were consulted. The guide was retrieved directly as public HTML when the browsing tool could not render it; its title is *Analyze word counts and match statistics* and it states a review date of 7 September 2026.

Endpoint versions below were checked against the current [official latest OpenAPI schema](https://developers.phrase.com/openapi/phrase-tms-latest.json) and endpoint Markdown definitions on 3 November 2026. Schema SHA-256: `675971b290359efc0c2354027003a0fcacb3b7e82f6d8fcd3f39ceb9bc1727cd`. Some rendered/cached reference pages still show older versions, notably v1 for net-rate detail; the directly fetched schema and [current endpoint definition](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/get-net-rate-scheme.md) specify v2. Verify response contracts against the organization's instance before implementation is accepted.

Use a separately configured TMS host. The documented EU base is `https://cloud.memsource.com/web`; the US deployment uses `https://us.cloud.memsource.com/web`. Append each complete versioned `/api2/...` path below, rather than assuming one global API version. See [API authentication](https://support.phrase.com/hc/en-us/articles/5709662181404-API-Authentication-TMS).

### Read-only endpoint map

Every operation in this table is GET. Authentication token exchange is a separate operation, not an analysis mutation.

| Purpose | Path | Use and official reference |
| --- | --- | --- |
| Current authenticated identity | `/api2/v1/auth/whoAmI` | Confirm identity/role and intended organization without logging personal details. [Reference](https://developers.phrase.com/en/api/tms/latest/authentication/who-am-i) |
| Project discovery | `/api2/v1/projects` | Paginate all accessible projects in scope. `clientId` is a numeric ID, not a UID; names are partial-match search hints. Explicit archive policy. [Reference](https://developers.phrase.com/en/api/tms/latest/project/list-projects) |
| Project metadata | `/api2/v1/projects/{projectUid}` | Role-dependent metadata, including client where visible. Archived projects can return 404. [Reference](https://developers.phrase.com/en/api/tms/latest/project/get-project) |
| Client discovery, if needed | `/api2/v1/clients` | Resolve IDs before filtering; visibility can be restricted. [Reference](https://developers.phrase.com/en/api/tms/latest/client/list-clients) |
| Project workflow steps | `/api2/v2/projects/{projectUid}/workflowSteps` | Enumerate actual workflow levels. [Reference](https://developers.phrase.com/en/api/tms/latest/project/get-workflow-steps) |
| Jobs in a project | `/api2/v2/projects/{projectUid}/jobs` | Paginate per workflow level; omitted `workflowLevel` defaults to 1. Optional `trashed=true` is a separate recycle-bin inventory. [Reference](https://developers.phrase.com/en/api/tms/latest/job/list-jobs) |
| Job metadata | `/api2/v1/projects/{projectUid}/jobs/{jobUid}` | Languages, timestamps, workflow, source `jobReference`, and split segment bounds. [Reference](https://developers.phrase.com/en/api/tms/latest/job/get-job) |
| Project analyses | `/api2/v3/projects/{projectUid}/analyses` | Primary discovery; filters include name, UID, and `onlyOwnerOrg`; supports date-created sort, but not an arbitrary date window. [Reference](https://developers.phrase.com/en/api/tms/latest/project/list-analyses-by-project) |
| Job analyses | `/api2/v3/projects/{projectUid}/jobs/{jobUid}/analyses` | Reverse discovery and coverage audit; one analysis can appear for many jobs. [Reference](https://developers.phrase.com/en/api/tms/latest/analysis/list-analyses) |
| Full analysis | `/api2/v3/analyses/{analyseUid}` | Primary language-pair metadata, raw counts, net counts, categories, settings, warnings, readiness, and estimate flags. [Reference](https://developers.phrase.com/en/api/tms/latest/analysis/get-analysis) |
| Individual language part | `/api2/v1/analyses/{analyseUid}/analyseLanguageParts/{analyseLanguagePartId}` | Optional targeted retrieval; v1 is not schema-equivalent to v3. [Reference](https://developers.phrase.com/en/api/tms/latest/analysis/get-analysis-language-part) |
| All analyzed jobs in a language part | `/api2/v1/analyses/{analyseUid}/analyseLanguageParts/{analyseLanguagePartId}/jobs` | Paginated membership with per-job counts; needed even when v3 detail embeds jobs. [Reference](https://developers.phrase.com/en/api/tms/latest/analysis/list-jobs-of-analyses) |
| One analyzed job | `/api2/v1/analyses/{analyseUid}/jobs/{jobUid}` | Job raw/net counts; useful for targeted retry or inspection, not required for every job if the membership response suffices. [Reference](https://developers.phrase.com/en/api/tms/latest/analysis/get-jobs-analysis) |
| Native analysis export | `/api2/v1/analyses/{analyseUid}/download?format=JSON` | Also supports `CSV`, `CSV_EXTENDED`, `LOG`; requires download-analysis permission. [Reference](https://developers.phrase.com/en/api/tms/latest/analysis/download-analysis) |
| Net-rate scheme discovery | `/api2/v1/netRateSchemes` | Optional paginated lookup; internal Admin/Project Manager role required. [Reference](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/list-net-rate-schemes) |
| Associated scheme detail | `/api2/v2/netRateSchemes/{netRateSchemeUid}` | Read the scheme referenced by an existing analysis, not just the current organization default. [Current definition](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/get-net-rate-scheme.md) |
| Workflow scheme entries | `/api2/v1/netRateSchemes/{netRateSchemeUid}/workflowStepNetSchemes` | Paginate workflow-specific references. [Reference](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/list-schemes-for-workflow-step) |
| Workflow-specific rates | `/api2/v1/netRateSchemes/{netRateSchemeUid}/workflowStepNetSchemes/{netRateSchemeWorkflowStepId}` | Additional setup-server permission can be required. [Reference](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/get-scheme-for-workflow-step) |

The relevant collection endpoints use zero-based `pageNumber` and `pageSize` up to 50. Follow `totalPages` and reconcile `totalElements`; do not stop after one page or confuse page numbers with one-based workflow levels. Project-list relative-hour filters concern jobs, so they are not a safe substitute for analysis creation-date filtering. Unlisted query parameters can be rejected. [Project list](https://developers.phrase.com/en/api/tms/latest/project/list-projects), [analysis list](https://developers.phrase.com/en/api/tms/latest/project/list-analyses-by-project).

### Metadata, counts, and category semantics

Preserve analysis UID separately from numeric `id` and project-local `innerId`. Store type, creation timestamp, creator reference, provider, project UID, associated scheme UID, settings, `outdated`, `importStatus`, and warnings. Each language part has its own ID, source/target locales, TM references, `data` and `discountedData`. Preserve `available` and v3 `estimate` flags. Do not infer languages from product mappings. [Analysis detail](https://developers.phrase.com/en/api/tms/latest/analysis/get-analysis).

| Requested statistic | Retrieval and interpretation |
| --- | --- |
| Raw words | `analyseLanguageParts[].data.all.words`; raw means before net-rate weighting, **within that analysis's settings and selection**. It does not guarantee all content in the project. |
| Discounted words | Corresponding `discountedData.all.words`; retain both totals and matching category/band values. Missing scheme or unavailable data must not silently become zero net words. |
| TM | `transMemoryMatches`: `match101`, `match100`, `match95`, `match85`, `match75`, `match50`, `match0`. |
| MT | `machineTranslationMatches`: 100, 95, 85, 75, 50, 0 bands. This describes analyzed MT matches/effort or estimates according to type/settings, not consumed AI units. |
| Repetitions | Separate `repetitions` counts. These are counted occurrences, not an extra amount to add to `all`. |
| Internal fuzzies | `internalFuzzyMatches`, where available. Preserve inclusion/separation settings; disabled or merged categories are not proof of no fuzzies. |
| NT | `nonTranslatablesMatches`; retain the exact endpoint version and field names because v1/v3 band shapes differ. |
| Other units | Preserve words, characters, segments, normalized pages, percentages, and editing-time fields as separate measures; do not substitute them for one another. |

The [analysis guide](https://developers.phrase.com/en/guides/analysis) defines TM `match95` as 95-99%, `match85` as 85-94%, `match75` as 75-84%, `match50` as 50-74%, and `match0` as 0-49%; `match101` is an in-context match, not 101% billable words. Preserve original keys alongside presentation labels. Counts are language-sensitive; analysis words differ from MT character consumption and subscription processed words. [Word-count rules](https://support.phrase.com/hc/en-us/articles/5709712007708-Analysis-TMS).

**Version gap requiring a contract check:** v3 NT data declares `match0/50/75/85/95/100`, while v1 job and language-part schemas declare only NT `match99/100`; v1 also lacks the v3 `estimate` flag. Net-rate settings include `nt99`. Do not relabel v1 `match99` as v3 `match95`, assume absent bands are zero, or promise a lossless job-level v3 breakdown. Retain all actual response fields, distinguish language totals from job detail, and report a schema-coverage limitation until real read-only samples and the native JSON download establish what can be reconciled. [Job detail](https://developers.phrase.com/en/api/tms/latest/analysis/get-jobs-analysis), [language detail](https://developers.phrase.com/en/api/tms/latest/analysis/get-analysis-language-part), [current schema](https://developers.phrase.com/openapi/phrase-tms-latest.json).

Settings and type are part of every comparison. Keep default/pre-analysis, post-editing, target-unit, provider-specific, continuous, and compare analyses separate. Preserve source-versus-target counting, included/excluded locked/confirmed/number segments, MT/NT inclusion, and internal-fuzzy handling. Missing fields stay unknown rather than receiving assumed defaults. An outdated analysis remains an explicitly flagged historical result; extraction must not recalculate it. [Analysis guide](https://developers.phrase.com/en/guides/analysis).

### Net rates, leverage, and native exports

Prefer Phrase's returned `discountedData`; no local application of a new scheme and no PUT to associate one. Capture scheme/rate metadata as observed at extraction time. A rate of 20 means 20% of the raw category count remains as net work; it is not an 80% multiplier. Scheme/workflow precedence and edition-dependent NT behavior must be verified before any optional local reconciliation. Missing permission to read a scheme should preserve available returned net counts while marking rate provenance unavailable. [Net-rate schemes](https://support.phrase.com/hc/en-us/articles/5709665578908-Net-Rate-Schemes-TMS), [workflow rate permissions](https://developers.phrase.com/en/api/tms/latest/net-rate-scheme/get-scheme-for-workflow-step).

Proposed derived values, only for compatible, available raw/net pairs with a known associated scheme:

```text
leverage_words = raw_words - net_words
leverage_percent = 100 * leverage_words / raw_words    # only if raw_words > 0
```

Compute percentages from summed compatible counts, never by adding/averaging API percentages. Missing or zero denominators produce null percentages. Preserve fractional counts and numerical precision; round only for display. Negative or inconsistent differences become quality exceptions, not silently clamped values.

Phrase's Leverage dashboard uses raw-minus-net words, the latest default analysis per job, and analysis ownership. It excludes internal-fuzzy analyses, deleted analyses, missing schemes, and zero-leverage cases. A comprehensive REST inventory should retain accessible internal fuzzies and zero-leverage records, and expose a separately labeled dashboard-comparison view. Exact parity also depends on ownership, date/filter semantics, and analytics refresh delay; it is not guaranteed. [Dashboard definition](https://support.phrase.com/hc/en-us/articles/6554447243036-Analytics-Dashboards-TMS).

The native download is an attachment, including JSON. It differs from v3 REST JSON: weighted counts can be exported when a scheme is attached, JSON has a distinct nested source-category layout, and CSV/LOG combine match sources. Native JSON can supplement reconciliation but cannot replace the REST raw/net pair. It can identify jobs by filename rather than stable UIDs, so duplicate filenames prevent an assumed one-to-one join. Do not remove a scheme to obtain an unweighted download. [Export guide](https://developers.phrase.com/en/guides/analysis), [download operation](https://developers.phrase.com/en/api/tms/latest/analysis/download-analysis).

## 5. Credential access investigation

**Result: TMS leverage access is unverified and cannot be established from the existing dashboard credentials.** The inspected local `.env` has only the four dashboard URL/referer keys; the workflow references those plus SMTP credentials. No TMS token/login/OAuth configuration is present. GitHub secret values and any additional unreferenced secrets were not available for verification. An analytics token working for AI reporting does not establish project visibility, analysis-read rights, download rights, or scheme-read rights.

Phrase supports Platform Bearer access tokens, TMS `ApiToken` authentication, and OAuth. For unattended Actions, prefer a suitably permissioned Platform service account if available, using client credentials to obtain an access token for TMS; alternatively exchange a Platform API token for a Bearer JWT. Respect returned `expires_in`, refresh in memory, and keep authentication separate from dashboard JWT handling. TMS `POST /api2/v3/auth/login` is an alternative only if organization policy allows it, using the returned token with `Authorization: ApiToken ...`. No credentials were requested or tested during this review. [TMS authentication](https://developers.phrase.com/en/api/tms/latest/authentication), [Platform authentication and service accounts](https://developers.phrase.com/en/api/platform/authentication).

Future read-only preflight, once a credential is securely configured:

1. Validate the configured region/host and obtain a short-lived token without logging it. `whoAmI` must identify the intended account/organization; a valid token alone is insufficient.
2. List projects and fetch at least one known in-scope project and its jobs. Compare visibility with a known expected project set; HTTP 200 with an empty collection does not prove complete access.
3. List that project's existing analyses, fetch a completed analysis, and paginate all analyzed jobs. Test a known shared project if shared work is in scope.
4. Read its associated net-rate scheme/workflow entry where permitted. Test native JSON download separately because it has a distinct download permission. Scheme/workflow access can require internal Admin/PM and additional setup rights; analysis visibility alone is insufficient.
5. Record only capability/status summaries. Treat 401 as invalid/expired authentication, 403 as denied capability, and 404 as absent/inaccessible/archived rather than automatically no analysis. Verify ambiguous cases with known metadata.

All analysis/project operations in preflight are GET. Do not restore archived projects, grant roles, create a sample analysis, attach a scheme, or recalculate to make a probe succeed. The auth token endpoint's POST is the sole expected non-GET network operation in the proposed read-only extractor.

## 6. Proposed leverage extraction architecture

### Separate execution and reporting contracts

Keep the current `main.py` AI execution intact initially. Add `leverage_main.py` plus a small `leverage/` package containing `config.py`, `auth.py`, `client.py`, `discovery.py`, `normalize.py`, `selection.py`, and `export.py`. These are proposed files, not files created by this review.

```text
Independent leverage entry point
  -> explicit config + authentication preflight
  -> project/workflow/job inventory
  -> project analysis discovery + job coverage audit
  -> deduplicated analysis detail + complete language-part job pagination
  -> immutable response snapshots + version-aware normalized records
  -> explicit selection policy + quality reconciliation
  -> separate leverage XLSX/CSV/JSONL + coverage manifest
```

Do not merge job-level leverage records into the current project-name AI table: it would repeat the same AI totals across many jobs. Any future combined report must aggregate both independently to a validated common grain and keep distinct units and periods.

### Discovery, extraction, and resilience

1. Resolve project scope using explicit UIDs and optional numeric client IDs. Use names only to find candidates, then confirm exact identity. Preserve unmapped projects. Inventory archived projects when requested and flag inaccessible archive data; never restore them.
2. Enumerate workflow steps and paginate jobs per step. Default reporting may select workflow level 1, but extraction should inventory every configured level so other work is visible rather than silently omitted. Keep active and optionally trashed-job inventories separate.
3. Paginate project analyses, deduplicate on `(instance, analysis_uid)`, fetch v3 detail once, and preserve all accessible analysis types in the inventory. Use job-analysis discovery as a coverage audit/fallback and retain jobs with no visible analyses.
4. For every language part, always paginate its analyzed jobs. The embedded `jobs` array is capped at 100. Reuse returned per-job data instead of fetching every single-job endpoint again; reserve targeted GETs for gaps and contract checks. Distinguish analyzed-job `uid` from source `jobUid`/`jobReference`; do not substitute filenames, `innerId`, or a parent job UID in endpoint paths. [Membership](https://developers.phrase.com/en/api/tms/latest/analysis/list-jobs-of-analyses).
5. Store response provenance, schema/endpoint version, extraction time, status, checksum, and normalized records. Preserve unknown fields and null/missing categories. Schema drift should create a quality exception rather than silently discard fields.
6. Use a dedicated `requests.Session`, identifiable application user-agent, explicit connect/read timeouts, conservative request pacing, bounded retries for transient transport/429/5xx failures, and `Retry-After` where returned. Start sequentially; only add bounded concurrency after measurement. Never retry 403 indefinitely. Refresh expired authentication at most once per failed request, then fail with a sanitized message.
7. Observe both quota and concurrent-request limits. Official guidance describes a 6000-request/minute quota for logged-in users and distinct concurrency limits; do not use that number as the application's target throughput. Configure below the tenant's effective allowance. [Rate limits](https://developers.phrase.com/en/api/tms/latest/handling-api-rate-limits), [concurrent limits](https://developers.phrase.com/en/api/tms/latest/handling-concurrent-api-limits).
8. Bounded GET polling may wait for an already-running analysis to complete; record timeout/running/error status without starting work. Mark partial runs explicitly and exit nonzero on required coverage failure. Optional enrichment failures should be distinct from missing core counts.

Approximate request cost is project/job/analysis list pages, one v3 request per unique analysis, all membership pages, plus metadata/scheme requests cached by UID. Estimate this before enabling a large backfill. Pagination is not a transactional snapshot: record inventory changes and deduplicate/reconcile a second bounded discovery pass if counts move during extraction.

### Record identity, selection, and time

Keep separate tables for analysis metadata, language-pair totals, analysis/job membership, job-band measures, rate snapshots, and coverage exceptions. Include instance/organization context, project UID, analysis UID, language-part ID, analyzed-job UID, source-job reference, workflow level, provider identity, analysis type/settings fingerprint, endpoint version, and count basis. Preserve both source and analyzed job identifiers and split bounds when available.

Candidate measure keys:

- Language total: `(instance, analysis_uid, language_part_id, category, band, raw_or_net, unit)`.
- Job detail: the same plus `analyzed_job_uid`; store source-job and workflow identifiers as dimensions.
- Historical snapshot: add `extracted_at` and response checksum; rerunning a snapshot must not append duplicate business measures into a current-state total.

`all` totals and job details are different grains: never sum both. Retain every analysis in the inventory, but select one compatible analysis per analyzed job/workflow/language/count basis for summary totals. Recommended first summary policy: consider `PreAnalyse` with a verified source-unit basis inside the requested analysis-created window; choose the latest by parsed `dateCreated` then UID for a deterministic tie-break. Include that candidate's counts only if it is complete, available, and not outdated. Otherwise record a coverage exception instead of silently falling back to an older result. Record rejected candidates and selection reason. Provider/continuous/target/post-editing/compare types remain separate views until their semantics are explicitly mapped. Any alternative fallback policy must be explicit.

When one analysis spans several jobs and another covers only some of them, selection is per job, not per project or whole analysis. If job-level raw/net category detail cannot support a trustworthy selected-job summary because of the v1/v3 gap, export complete v3 language totals **per analysis** and label the job summary limited; do not prorate language totals across filenames. Exact, fully categorized per-job reporting remains an acceptance gate, not an assumed capability.

Proposed period contract:

- Explicit ISO-8601 `period_start` inclusive and `period_end` exclusive, converted to UTC. Freeze the run timestamp once. Default new leverage window: January 1 of the run year through that timestamp, with `period_basis=analysis_created_at`.
- Filter analysis dates locally after complete scoped discovery; do not filter on project creation or relative job hours and thereby lose analyses on long-running projects.
- Record `analysis_created_at`, job creation/update timestamps, and `extracted_at` separately. A recent analysis of an old job belongs to the analysis-created window; that is an analysis report, not translation throughput.
- An optional latest-snapshot view across all dates must be labeled separately. A live API read cannot reconstruct an earlier immutable state after an analysis or scheme changed; historical as-of reporting requires previously saved snapshots.
- Keep the existing AI `thisyear` behavior unchanged. Document its upstream date basis as unverified; matching date labels do not guarantee comparable populations.

## 7. Proposed exports and persistence

| Output | Recommendation |
| --- | --- |
| Leverage workbook | Separate `Leverage_Statistics_<UTC-run-id>.xlsx`, outside the existing root XLSX globs. Sheets for selected job summary, per-analysis language totals, category bands, analysis inventory, rates, coverage, and run metadata. Mark limitations and counts basis in the workbook itself. |
| CSV | UTF-8 long-form measures and separate dimension/coverage files. Include stable IDs, units, grain, raw/net flag, category, original band, type, period basis, and selection flag. Avoid aggregate TOTAL rows in machine-readable data. |
| JSON/JSONL | Sanitized complete REST response snapshots plus normalized records and a schema-versioned run manifest. Prefer this for audits, future parsing, and precise counts. Native download JSON is an optional separate artifact with its own format tag. |
| Persistent history | Begin with versioned run archives in an approved private artifact/object store with explicit retention. Local SQLite is optional for idempotent current-state upserts, but Actions runners are ephemeral: persistence must be deliberately uploaded/restored. Do not rely on a cache as the sole audit history. |

The manifest should state scope, effective period, identity/organization check outcome, schema fingerprint, selection policy, discovered/selected/missing/denied/outdated/running counts, expected versus fetched pages/jobs, category support, optional enrichment failures, run completeness, and checksums. No credentials, headers, or credential-bearing request URLs. File-level incomplete status must survive a successful file write.

Store leverage outputs in a dedicated directory, e.g. `reports/leverage/<run-id>/`, so existing email ZIP globs cannot accidentally distribute them. New leverage delivery and retention must be explicit. Use decimal-aware normalization for calculations; preserve original JSON numerics and avoid truncating weighted words to integers. Split large Excel detail sheets before the row limit and keep machine-readable exports authoritative.

## 8. Required changes, only after proposal review

| Area | Proposed implementation change |
| --- | --- |
| New extractor | Add the separate entry point/package described above, with GET-only content operations and explicit exit/completeness status. |
| Configuration | Add nonsecret scope/period/archive/ownership/workflow/selection/retry options, preferably in a separate leverage settings file. Keep dashboard variables unchanged. |
| New secrets | Select one supported auth mode. For Platform service accounts, use separate `PHRASE_TMS_CLIENT_ID` / `PHRASE_TMS_CLIENT_SECRET`; for token exchange, a separate `PHRASE_PLATFORM_API_TOKEN`. Names are proposed only. Configure region/TMS base URL separately; do not reuse dashboard URL secrets. |
| Mapping | Reuse current exact-name mapping as enrichment initially, then add explicit project-UID mappings after ambiguity checks. Do not silently edit existing AI mappings or the unused language list. |
| Exports | Add separate leverage exporter with named numeric totals, proper Excel column utilities, consistent run IDs, and null/quality handling. Share helpers only after AI regression fixtures protect behavior. |
| Tests | Add offline fixtures, contract checks, coverage assertions, and opt-in credentialed GET-only integration tests. |
| Dependencies | Initial leverage implementation can use the existing HTTP/data/export stack and standard library. Pin a validated Python 3.10-compatible environment separately; any dependency changes need regression checks. No new SDK is assumed. |
| Git ignore / docs | Ignore generated outputs/snapshots/caches and document secret setup, units, period semantics, access preflight, and actual artifact delivery. Correct README inconsistencies as a later reviewed change. |
| Workflow | After successful manual validation, add an isolated leverage job or separately dispatched validation workflow. Keep `0 16 * * 0` unchanged. Preserve the existing AI job and email steps. Upload leverage artifacts explicitly with private access/retention policy; decide new delivery separately. |

Prefer a separate leverage job so leverage permission/API failures cannot suppress the existing AI emails. Do not insert leverage work before those emails in the current single job. Later AI hardening can address silent errors and duplicate merges, but those are independent behavior changes with their own fixtures and review.

## 9. Duplicate, missing-record, and API limitations

| Risk | Required control |
| --- | --- |
| One analysis discovered through many jobs | Deduplicate by instance + analysis UID before fetching/aggregating. |
| Repeated/default/provider/post-editing analyses overlap | Inventory all; explicit compatible per-job selection, separate types/providers/workflows. |
| Same source across workflow steps or split/continuous jobs | Keep analyzed part UID, source reference, workflow level, segment bounds, and snapshot time. Never deduplicate by filename or simply sum every workflow. |
| Membership embedded list capped at 100 / multiple pages | Always paginate membership and all discovery lists; reconcile counts and reject silent truncation. |
| Analysis settings exclude content or omit IF/MT/NT | Carry settings/availability through exports; distinguish disabled, unavailable, unknown, and true zero. No generation or recalculation to fill gaps. |
| v1 job schema loses v3 category/estimate detail | Version-aware parsing, exact source snapshots, explicit detail coverage; native JSON spike and Phrase clarification if unresolved. |
| Archived/deleted/unshared content | Inventory visible references and denial/404 evidence. GET project/jobs can return 404 for archives; never restore them automatically. Permanently deleted records may be irretrievable. |
| Role, organization, client, or ownership visibility | Verify against a known scope and shared-work sample; empty lists do not imply zero workload. `onlyOwnerOrg` is an analysis-ownership choice, not a project/client filter. |
| Mutable analysis or scheme under same UID | Persist extraction timestamp/checksum and observed rate snapshot. No assumption that `dateCreated` is a last-modified timestamp. Periodic full reconciliation rather than a creation-only watermark. |
| Running/error/outdated analyses | Preserve status, bounded read-only polling, coverage exceptions, no implicit recalculation. |
| Page contents change during traversal | Stable supported sorting, UID deduplication, count checks, bounded reconciliation pass, and partial-run flag. |
| Dashboard-versus-REST differences | Keep inventory scope broader; compare only matching settings, ownership, period, scheme, and refresh snapshot. No promised equality with AI usage or dashboard wordcounts. |

The API cannot create comprehensive historical leverage where no suitable analysis existed. Comprehensive here means all **accessible existing** records plus explicit coverage gaps. A broader data product or Phrase support clarification may be needed for deleted history or a lossless per-job schema; it is not supplied by reusing an analytics JWT.

## 10. Security considerations

- Keep `.env` ignored and all new credentials in protected runtime secrets. Do not embed tokens in plans, fixtures, output manifests, URLs, or command arguments. Never paste copied authenticated cURL requests into a third-party converter; the existing README suggests this and should be updated in a later documentation change.
- Enforce HTTPS and a configured trusted Phrase region/host. Restrict authenticated requests/redirects to approved hosts; do not follow arbitrary response-provided URLs with authorization headers.
- Use a dedicated identity with the minimum effective project/analysis access. Do not assume a `tms:read` scope exists or grant Admin merely for convenience. Guard GET-only content operations in the client even if the account can write.
- Log endpoint templates, status, counts, and safe request IDs, not raw exception URLs, auth headers, token-exchange bodies, creator email addresses, or full payloads.
- Analysis metadata can contain confidential client/project/filename/provider information. Minimize exported personal data, restrict artifact access and retention, and keep leverage reports out of existing recipient ZIPs until delivery is explicitly configured.
- Neutralize spreadsheet formula injection for untrusted text cells and validate filenames/path components. Keep numeric cells numeric and preserve original text safely in structured archives.
- Preserve current `contents: read` and action SHA pinning. Do not expose production secrets to pull-request code; integration checks should be manually gated or run in a protected environment.

## 11. Baseline results and proposed test strategy

### Baseline recorded on 3 November 2026

Local environment: Windows, Python **3.13.5**; installed `requests 2.32.5`, `pandas 3.0.3`, `openpyxl 3.1.5`, `pytest 9.1.1`. `python-dotenv` and PyYAML are not installed locally. Actions specifies Python 3.10 and installs its requirements fresh, so this is not an exact CI environment reproduction. No dependencies were installed or upgraded during review.

| Check | Result |
| --- | --- |
| Compile `main.py` in memory | PASS; no bytecode/source modification |
| Parse and inspect mapping JSON | PASS; 24 product entries, 118 distinct project mappings; six declared locales versus seven actually mapped, including `ko-KR` |
| `python -m unittest discover -v` | **0 tests; `NO TESTS RAN`, exit 1** on this interpreter. No existing automated test coverage can be claimed. |
| Mocked normal extraction/export | PASS; five temporary workbooks, expected column order, mapping, totals (3.5 AI units / 120 words), bold TOTAL row |
| Mocked wordcount HTTP 503 | Existing behavior confirmed: AI exports survive, wordcount becomes zero |
| Mocked AI HTTP 401 | Existing behavior confirmed: normal function return, no reports, no raised failure |
| Mocked empty AI + nonempty wordcounts | Existing behavior confirmed: merge failure is swallowed, no reports |
| Mocked duplicate project names | Existing behavior confirmed: 2x2 join creates four rows, inflating AI total 3 to 6 and wordcount 30 to 60 |
| Live workflows / Phrase API / email | Not run; no account-level access or live result correctness verified |

The five characterization cases used temporary directories and synthetic `example.invalid` URLs, cleared inherited configuration, disabled dotenv loading, mocked GET responses, and blocked real session requests. The harness was temporary, not added to the application. An initial temporary-directory cleanup attempt hit a Windows current-directory lock; the harness was corrected to restore its working directory before cleanup, and the full rerun completed successfully. These checks characterize existing behavior; they do not make the confirmed failure modes acceptable. The workflow received a manual source review, not an execution or an automated YAML/action validation.

### Implementation acceptance tests

1. **AI regression:** freeze representative dashboard fixtures; compare workbook values, columns, total rows, mappings, filenames, and routing against current successful behavior. Verify leverage failure/missing credentials cannot prevent the existing AI job and emails. Test current failure modes separately before changing them.
2. **REST contracts:** fixtures for real versioned response shapes, raw/net availability, estimates, fractional counts, source/target bases, every type, every supported band, and unknown fields. Explicitly test NT99 versus NT95 schemas and loss-of-detail flags; never normalize them as interchangeable.
3. **Completeness:** more than 50 projects/analyses/jobs, more than 100 embedded jobs, final short/empty pages, changing totals, workflow levels beyond 1, shared ownership, archived/trashed/denied jobs, and jobs without analyses. Assert manifest counts, uniqueness, and visible missing-data reasons.
4. **Selection and aggregation:** overlapping job sets, repeated analyses, duplicate filenames/project names, providers, workflows, splits and continuous updates; deterministic ties, excluded latest candidates, raw/net category reconciliation, and separation of language totals from job rows. Recompute percentages from aggregate counts.
5. **Dates and persistence:** year boundaries, timezone offsets/DST inputs, inclusive/exclusive endpoints, old projects with new analyses, reruns, mutated records under the same UID, restart/retry idempotence, and optional snapshots without claims of historical reconstruction.
6. **Resilience and safety:** 401 refresh once, denied/404 capabilities, 429 with and without retry headers, timeouts, 5xx, malformed/202/incomplete results, exhausted retry budgets, log redaction, filename/formula safety, and a request spy that rejects all content mutations. Unit tests use no production credentials.
7. **Read-only integration and reconciliation:** manually use an existing known completed analysis with a securely configured credential; compare language totals, job membership, raw/net categories, and native JSON with the UI without modifying anything. Include a shared project, a multi-workflow project, and an analysis with IF/NT/MT where they already exist. Record unsupported cases as gaps. Compare with dashboards only after aligning their scope and refresh time.
8. **Workflow:** execute a manual leverage validation run with no existing email changes; inspect artifact completeness, retention, redaction, and AI isolation. Confirm the scheduled cron string and all existing AI output contracts remain unchanged.

## 12. Proposed sequence and decisions before implementation

1. Review this proposal and confirm the intended project/client scope, analysis-created versus other period basis, ownership/workflow selection, and whether dashboard parity or the broader inventory is the primary summary.
2. Securely configure a separate supported TMS credential and run the GET-only capability preflight. Actual account access remains unresolved until then; do not send credentials in chat.
3. Perform a small read-only contract spike on existing analyses to resolve the v1/v3 job-detail gap, ID mapping, net-rate metadata permissions, and any native-download joins. Report limitations before promising fully categorized job-level summaries.
4. Implement fixtures and the independent extractor/exporter, then reconcile a bounded sample. Keep the existing application/schedule and AI output contracts intact.
5. After successful manual review, add isolated Actions execution and explicit leverage artifacts/delivery. Preserve the existing weekly schedule and AI reporting.

This review stops at the proposal. No implementation, workflow edit, or Phrase analysis mutation is included.
