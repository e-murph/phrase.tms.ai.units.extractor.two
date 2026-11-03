# Validated leverage reports

These reports extend the independent leverage extractor. Existing AI XLSX outputs, filenames, data, emails, and schedule are unchanged. Authentication and read-only access requirements remain in [LEVERAGE_USAGE.md](LEVERAGE_USAGE.md). No new credentials or Phrase analyses are created.

## Files and naming

Every successful export writes these files to the dedicated leverage output directory, alongside the existing normalized CSV/JSONL and workbook outputs:

| File prefix | Purpose |
| --- | --- |
| `Detailed_Phrase_Leverage_Stats_` | One row per unique analysis/job/language/origin/match-category observation; word, character, and segment measures share that row. |
| `Project_Phrase_Leverage_Stats_` | One row per project, source/target language pair, and reporting scope. |
| `Client_Phrase_Leverage_Stats_` | Project summaries aggregated by stable client identity, language pair, period, and scope. |
| `Master_Phrase_Leverage_Stats_` | Self-contained structured JSON with metadata, raw/discounted categories, summaries, selection, rates, and validation. |
| `Validation_Phrase_Leverage_Stats_` | CSV audit of category and membership reconciliation, availability, and duplicate/ambiguous records. |

Each name ends in `<profile-id>_YYYY-MM-DD.csv` (or `.json`), following the existing underscore/date convention. The UTC date is frozen at extraction start. Profile IDs distinguish configurations and periods. Same-profile/day reruns replace files rather than append rows; earlier named snapshots remain available for historical comparison. `manifest.json` names the current five report files and their SHA-256 checksums. Read that list instead of concatenating every dated file in the directory.

The reporting and normalized exports describe snapshots, rather than transactions. Compare snapshots for the same scope, selection policy, count basis, and compatible settings; never sum multiple snapshots of the same business records. Different timestamps/checksums do not by themselves imply a volume change. Prior historical account state cannot be reconstructed after analyses or schemes change without a saved snapshot.

## Reporting scope and avoiding double counts

The reporting default is **workflow level 1**, source-unit `PreAnalyse`, inside the configured analysis-creation window. The underlying extraction still inventories every accessible workflow and analysis type.

```console
python leverage_main.py --start 2026-01-01 --end 2026-11-01 --report-workflow-level 1
python leverage_main.py --period thisyear --report-workflow-level 1 --report-workflow-level 2
python leverage_main.py --period all --summary-type PreAnalyse --summary-type PostAnalyse
```

Repeated workflow levels and analysis types produce **separate scopes**, never a combined baseline. `scope_id`, `workflow_level`, `analysis_type`, `count_basis`, `period_start`, `period_end`, and `period_basis` identify each summary. The manual leverage workflow has a `report_workflow_levels` input, default `1`. A scope includes organization/region, project/client filters, ownership/archive policy, selection policy, and validation tolerance.

Within a scope, existing selection chooses the latest compatible analysis per analyzed job and type. Newer inaccessible/outdated/running candidates do not fall back to older counts. Reporting additionally requires category validation, consistent job UID/project/workflow attribution, and trustworthy membership/discovery. Known overlapping parent/split source ranges are excluded; known splits without verifiable lineage/bounds are excluded from validated totals. Repeated language-part IDs and multiple parts claiming the same analysis/job/language combination are flagged; their original data remains in structured JSON. Identical repeated inventory records are deduplicated. Reporting scope version 2 identifies the stronger validation introduced by the review.

Translation and revision can contain the same source content. Do not add different workflow scopes together as source volume. Detail includes all retrieved types and earlier analyses, with `selected_for_summary` identifying validated selections; filter to **one scope** before totaling detail. Project/client summaries already apply that rule. Default and post-editing data are never combined. The `all` control totals are omitted from detailed match rows so they cannot be added again to their component categories.

The job population is the currently discovered job/workflow inventory, not a job-created-in-period population. A job without an eligible analysis created inside the window counts as missing a valid analysis, even if an older analysis exists. Thus the reporting period measures analysis observations, not translation throughput. Unknown discovery cardinality produces null exact job counts, with separately labeled known lower bounds.

## Detailed CSV

Columns include client/project names and UIDs; analysis UID/name/date/type; job UID/filename; workflow name and metadata; source/target languages; original origin/category keys; gross/discounted/saved words; gross/discounted characters and segments; associated net-rate scheme; availability/validation flags; and reporting selection/scope.

`match_origin` maps the known keys to `TM`, `MT`, `NT`, `internal_fuzzy`, or `repetition`; `origin_key` preserves the original API key. Unknown origins retain their actual names. `match_category` preserves the actual band, including separate `match101` and `match100`. No unused bands are invented or filled with zeros. Repetition counts remain their own category.

`raw_metrics` and `discounted_metrics` retain other returned units and values, such as normalized pages, editing time, and API percentages. These are not added to word counts. The v1 NT99/100 job bands and v3 language NT bands remain distinguishable in JSON; no cross-version remapping or proportional allocation is performed.

Availability has two levels: whole count-block availability and presence of the particular category. For example, an absent raw TM101 field and a returned discounted TM101 value of zero produce null gross words, zero discounted words, null saved words, and `raw_category_not_returned`. An unavailable block is never treated as genuine zero. Actual observations with failed validation remain inspectable, are labeled, and are excluded from summary totals. Ambiguous structural duplicates are preserved only in JSON/audit data rather than arbitrarily merged.

## Project and client summaries

Required volume columns are `total_gross_words`, `tm_words`, `repetition_words`, `mt_words`, `non_translatable_words`, `internal_fuzzy_words`, `discounted_words`, `saved_words`, and `leverage_percentage`. Coverage fields include jobs covered, jobs missing valid analyses, jobs with valid discounted data, known inventory size, and completeness/status flags.

Full totals are deliberately strict:

- If a scoped job lacks validated raw analysis data or inventory is incomplete, full gross/category totals are null. `known_gross_words` and `known_*_words` retain the validated subset.
- If any covered job lacks valid discounted data or a known scheme association, full discounted/saved totals and full leverage percentage are null. `known_discounted_words`, `known_saved_words`, and the corresponding subset denominator remain available.
- Genuine returned zeros remain numeric zero. An entirely unobserved population or missing origin/band remains null, not a synthetic zero. Category maps contain only actual observed keys. A band missing for one project can therefore have an unknown full client-band total but a known subtotal.
- `tm_words` sums every band actually returned under `transMemoryMatches`, including `match0`; pricing thresholds are not inferred. The separate category maps preserve the bands needed for pricing analysis. Unknown match origins remain in those maps and are included in reconciliation.

Clients are joined by UID/numeric ID, including a consistent ID-to-UID alias when one project exposes only the ID. Names are display metadata, not identity. Same-named clients with different IDs remain separate. Projects without a resolvable client identity retain separate unresolved client rows rather than being combined into an invented client. Observed scheme references and settings fingerprints are included; mixed analysis settings are flagged for historical comparability. Full available scheme/workflow-rate snapshots are retained in JSON.

Client summaries consume project summaries, never the overlapping analysis inventory. A missing project is not silently dropped from an otherwise complete client total.

## Calculations, precision, and validation

All original decimal JSON numbers are parsed with `Decimal`, emitted as JSON **numbers**, and written to CSV without formatting round-off. Decimal summation/subtraction keeps the digits necessary for the original counts. Original raw and discounted categories remain separate. Excel may display fewer significant digits; CSV/JSON are authoritative for precise values.

Saved words are computed locally as **gross minus discounted**, only for available, compatible, validated data with a scheme association. Negative results are retained and flagged by extraction diagnostics rather than clamped.

Explicit raw or discounted estimates are not final validated counts. Original estimated observations remain inspectable with estimate flags. `discounted_estimated` detail rows preserve returned discounted words but have null calculated savings; project/client net totals and savings exclude them. Language-part estimate flags also apply to the associated v1 job observations, whose schema does not guarantee its own estimate flag.

The full leverage percentage is:

```text
100 * sum(valid saved words for the complete scoped population)
    / sum(valid gross words for that same complete population)
```

Its denominator is `leverage_denominator_words`, never net words, a TM-only subtotal, or the average of job percentages. It is null unless the full population has valid raw/net pairs and gross words are positive. `known_leverage_percentage` instead uses **only the valid paired subset**, whose gross words are `known_leverage_denominator_words` / `gross_words_with_discounted_data`. Both denominators are exported explicitly. Ratios use at least 50 decimal significant digits; original counts are never rounded to calculate them. A zero/unknown denominator yields null, not zero percent.

Validation checks raw and discounted blocks independently:

1. Sum all actual match-origin/category words, characters, and segments and compare with the corresponding Phrase `all` control value, at both job and language-part grain.
2. Reconcile available job totals **and individual category distributions** with their language-part results without adding the two grains together. A compensating movement between two categories cannot hide behind an unchanged `all` total. NT is checked at origin total because v1/v3 expose different band sets; original bands are not renamed.
3. Reject negative count partitions, missing/nonnumeric required values, overlapping language parts, and conflicting duplicate records from validated reporting. Explicitly unavailable data is a coverage gap.
4. Use an absolute tolerance of `0.000001` count units by default, configurable with `--category-tolerance`. The tolerance only controls validation; no reported value is rounded, adjusted, redistributed, or replaced.

The audit CSV records Phrase total, observed category/job sum, difference, unit, raw/discounted kind, context IDs, and result. `failed` or `unverifiable` checks make `report_validation.valid` false and the run partial, with exit code 2. Unavailable data produces explicit incomplete client coverage even when retrieval itself succeeds. `client_reporting_complete` additionally requires core completeness, complete validated raw/net project populations and resolved client identities. An incomplete client population now always produces export `status=partial`; expected unavailable data can still exit 0. The legacy selected-job inventory reflects extraction selection; the new reports additionally enforce category validation.

## Structured JSON

The version-2 master JSON contains project/job inventory; analysis metadata; complete original raw `data` and `discountedData` beneath each language part and job; settings and scheme snapshots; selection decisions; project/client summaries; validation; and coverage. Conflicting extra records remain in `unresolved_job_records`. Native JSON downloads remain explicitly separate from REST counts. Nulls, flags, original match bands, unknown categories, and unrounded values are preserved.

## Differences from Phrase Analytics

Phrase's Leverage Dashboard uses the latest default analysis per job and organization-owned analyses, and omits internal-fuzzy analyses, missing schemes, zero-leverage results, and deleted analyses. Its refresh cadence can also lag current API reads. [Official dashboard definition](https://support.phrase.com/hc/en-us/articles/6554447243036-Analytics-Dashboards-TMS).

These reports retain accessible internal fuzzies, genuine zero results, and missing-data diagnostics; choose the latest eligible analysis **inside an explicit analysis-created window**, and enforce additional validation. They expose workflow scopes and other types separately. Ownership defaults to all accessible analyses unless `--only-owner-org` is chosen. Counts/settings can change under an existing analysis UID, and API access to deleted/archived/shared data differs from Analytics history. Exact dashboard equality is therefore not promised: align period basis, ownership, workflow, settings, scheme state, snapshot/refresh time, and inclusion rules before comparing. Analysis leverage words also remain distinct from AI usage units and subscription wordcounts.

## Tests

Install `requirements-dev.txt`, then run `python -m unittest discover -v`. Mocked tests cover complete and partial aggregates, multilevel/type separation, overlapping analyses/language parts, 101-versus-100 matches, raw/net missing-band asymmetry, genuine zeros, unknown categories, decimal precision, all three reconciliation units, stable client identity, report headers/names/checksums, JSON preservation, and repeated execution. Workflow/mode tests and the existing AI workbook regression are included. The review adds an independently calculated two-project/two-language fixture, offsetting-category-error checks and estimate/metadata regressions. [LEVERAGE_VALIDATION_REPORT.md](LEVERAGE_VALIDATION_REPORT.md) records the 147 passing tests and manual reconciliation procedure. Live data validation still requires a securely configured REST credential; the existing dashboard/email secrets are unchanged.
