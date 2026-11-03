"""Replace current exports atomically per file; never append duplicate records."""

import csv
from io import StringIO
import hashlib
import os
from pathlib import Path
import re
import tempfile

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .numeric import dumps
from .normalize import record_id
from .reporting import DETAIL_COLUMNS, SUMMARY_COLUMNS, Reports


def json_text(value):
    return dumps(value)


def _replace(path, write):
    # All temporary files are created beside the explicitly selected output file.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        write(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_cell(value):
    if isinstance(value, (dict, list, tuple)):
        value = json_text(value)
    if isinstance(value, str):
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")):
            value = "'" + value
    return value


def _headers(rows):
    return sorted({key for row in rows for key in row}) or ["record_id"]


def _csv(rows, headers=None):
    stream = StringIO(newline="")
    headers = list(headers or _headers(rows))
    headers.extend(key for key in _headers(rows) if key not in headers)
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return stream.getvalue()


def _workbook(path, tables, manifest):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Run metadata")
    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 100
    for key, value in manifest.items():
        sheet.append([key, safe_cell(value)])
    sheet_names = {"selected_jobs": "Selected jobs", "selection": "Selection decisions",
                   "language_totals": "Analysis language totals", "job_analyses": "Job analysis inventory",
                   "analyses": "Analyses", "projects": "Projects", "jobs": "Jobs",
                   "measures": "Category measures", "net_rate_schemes": "Net rate schemes", "coverage": "Coverage"}
    for name, rows in tables.items():
        headers = _headers(rows)
        for offset in range(0, max(1, len(rows)), 1048575):
            suffix = "" if offset == 0 else " " + str(offset // 1048575 + 1)
            sheet = workbook.create_sheet((sheet_names[name] + suffix)[:31])
            sheet.freeze_panes = "A2"
            for index, key in enumerate(headers, 1):
                sheet.column_dimensions[get_column_letter(index)].width = min(60, max(16, len(key) + 2))
            sheet.append(headers)
            for row in rows[offset:offset + 1048575]:
                sheet.append([safe_cell(row.get(key)) for key in headers])
    workbook.save(path)


def export(extraction, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    # Invalidate a preceding successful manifest before replacing any data file.
    _replace(manifest_path, lambda p: p.write_text(json_text({"status": "writing", "core_complete": False}), encoding="utf-8"))
    tables = {name: sorted(rows.values(), key=lambda r: r["record_id"]) for name, rows in extraction.tables.items()}
    manifest = extraction.client.safe(extraction.manifest())
    manifest["output_semantics"] = "Current normalized files and same-profile/day reports are replaced, never appended; older named report snapshots remain separate."
    reports = Reports(extraction)
    manifest["report_validation"] = reports.validation.summary()
    manifest["core_complete"] = manifest["core_complete"] and manifest["report_validation"]["valid"]
    if not manifest["core_complete"]:
        manifest["status"] = "partial"
    manifest["client_reporting_complete"] = manifest["core_complete"] and bool(reports.projects) and all(
        r["gross_words_complete"] and r["discounted_words_complete"] for r in reports.projects) and all(
        r["client_identity_resolved"] for r in reports.clients)
    if not manifest["client_reporting_complete"]:
        manifest["status"] = "partial"
    profile_id = record_id(extraction.settings.public(), extraction.identity)[:12]
    day = extraction.started_at.strftime("%Y-%m-%d")
    files = {
        "detailed": f"Detailed_Phrase_Leverage_Stats_{profile_id}_{day}.csv",
        "project_summary": f"Project_Phrase_Leverage_Stats_{profile_id}_{day}.csv",
        "client_summary": f"Client_Phrase_Leverage_Stats_{profile_id}_{day}.csv",
        "validation": f"Validation_Phrase_Leverage_Stats_{profile_id}_{day}.csv",
        "structured_json": f"Master_Phrase_Leverage_Stats_{profile_id}_{day}.json",
    }
    manifest["report_files"] = files
    manifest["run_statistics"] = {
        "projects_processed": len(extraction.tables["projects"]),
        "analyses_found": len(extraction.tables["analyses"]),
        "successful_extractions": sum(row.get("raw_available") is True and row.get("gross_words") is not None and row.get("in_period") is True
                                      for row in extraction.tables["job_analyses"].values()),
        "missing_analyses": sum(row["known_jobs_missing_valid_analyses"] for row in reports.projects),
        "failed_api_requests": extraction.client.failed_request_count,
    }
    for name, rows, columns in (
        ("detailed", reports.details, DETAIL_COLUMNS),
        ("project_summary", reports.projects, SUMMARY_COLUMNS),
        ("client_summary", reports.clients, [c for c in SUMMARY_COLUMNS if c not in {"project_name", "project_uid"}]),
        ("validation", reports.validation.checks, ["check", "status", "project_uid", "analysis_uid", "job_uid",
                                                "count_kind", "unit", "phrase_total", "category_total", "difference", "message"]),
    ):
        _replace(output / files[name], lambda p, rows=rows, columns=columns: p.write_text(_csv(rows, columns), encoding="utf-8", newline=""))
    structured = extraction.client.safe({**reports.structured(), "run_metadata": manifest})
    _replace(output / files["structured_json"], lambda p: p.write_text(json_text(structured) + "\n", encoding="utf-8"))
    manifest["report_sha256"] = {name: hashlib.sha256((output / filename).read_bytes()).hexdigest()
                                 for name, filename in files.items()}
    for name, rows in tables.items():
        _replace(output / (name + ".jsonl"), lambda p, rows=rows: p.write_text(
            "".join(json_text(row) + "\n" for row in rows), encoding="utf-8"))
        _replace(output / (name + ".csv"), lambda p, rows=rows: p.write_text(_csv(rows), encoding="utf-8", newline=""))
    snapshots = sorted(extraction.client.snapshots.values(), key=lambda r: (r["kind"], r["key"]))
    _replace(output / "snapshots.jsonl", lambda p: p.write_text(
        "".join(json_text(row) + "\n" for row in snapshots), encoding="utf-8"))
    _replace(output / "Leverage_Statistics.xlsx", lambda p: _workbook(p, tables, manifest))
    _replace(manifest_path, lambda p: p.write_text(json_text(manifest) + "\n", encoding="utf-8"))
    return manifest
