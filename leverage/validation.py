"""Validate observed category partitions without changing Phrase's counts."""

from collections import defaultdict

from .normalize import _leaves, record_id
from .numeric import difference, number, total


UNITS = ("words", "characters", "segments")
ORIGINS = {"transMemoryMatches": "TM", "machineTranslationMatches": "MT",
           "nonTranslatablesMatches": "NT", "internalFuzzyMatches": "internal_fuzzy",
           "repetitions": "repetition"}


def categories(block):
    """One observed leaf-count object per original origin/band (no catalog filling)."""
    result = defaultdict(dict)
    if not isinstance(block, dict):
        return {}
    for path, value in _leaves(block):
        if len(path) >= 2 and path[0] != "all":
            result[(path[0], ".".join(path[1:-1]) or None)][path[-1]] = value
    return dict(result)


def category_maps(block):
    result = defaultdict(dict)
    for (origin, band), values in categories(block).items():
        result[origin][band or origin] = values.get("words")
    return dict(result)


class Validation:
    def __init__(self, extraction):
        self.extraction = extraction
        self.checks = []
        self.states = {}
        self.ambiguous_jobs = set()
        self.job_rows = {}
        identities = defaultdict(list)
        for row in extraction.tables["job_analyses"].values():
            key = tuple(row.get(k) for k in ("project_uid", "analysis_uid", "job_uid", "language_part_id"))
            previous = self.job_rows.get(key)
            if previous is not None:
                conflict = extraction.raw_counts.get(previous["record_id"]) != extraction.raw_counts.get(row["record_id"])
                if conflict:
                    self.ambiguous_jobs.update((previous["record_id"], row["record_id"]))
                self.event(row, "duplicate_job_record", "failed" if conflict else "warning", "Duplicate inventory record was not counted twice.")
                continue
            self.job_rows[key] = row
            identity = tuple(row.get(k) for k in ("project_uid", "analysis_uid", "job_uid", "source_language", "target_language"))
            identities[identity].append(row)
        for rows in identities.values():
            if len(rows) > 1:
                for row in rows:
                    self.ambiguous_jobs.add(row["record_id"])
                    self.event(row, "overlapping_language_parts", "failed", "One analysis/job/language appears in multiple language parts; detailed rows are excluded, originals retained in JSON.")
        for row in [*extraction.tables["language_totals"].values(), *self.job_rows.values()]:
            payload = extraction.raw_counts.get(row["record_id"], {})
            self.states[row["record_id"]] = {
                "raw": self.block(row, payload.get("data"), "raw"),
                "discounted": self.block(row, payload.get("discountedData"), "discounted"),
            }
        self.membership_checks()

    def event(self, row, check, status, message, **values):
        context = {key: row.get(key) for key in ("project_uid", "analysis_uid", "language_part_id", "job_uid", "source_language", "target_language", "grain")}
        self.checks.append({**context, **values, "check": check, "status": status, "message": message,
                            "record_id": record_id(row.get("record_id"), check, values.get("count_kind"), values.get("unit"))})

    def block(self, row, block, kind):
        if not isinstance(block, dict) or block.get("available") is not True:
            self.event(row, "category_total", "unavailable", "Count availability was not explicitly true; no zero was inferred.", count_kind=kind)
            return "unavailable"
        if block.get("estimate") is True:
            self.event(row, "estimated_counts", "unavailable", "Phrase marked these counts as estimates; originals are retained but excluded from validated figures.", count_kind=kind)
            return "estimated"
        control = block.get("all") if isinstance(block.get("all"), dict) else {}
        buckets = categories(block)
        results = []
        for unit in UNITS:
            expected = number(control.get(unit))
            values = [counts.get(unit) for counts in buckets.values()]
            if unit != "words" and unit not in control and all(unit not in counts for counts in buckets.values()):
                continue
            actual = total(values)
            delta = difference(actual, expected)
            if expected is None or actual is None:
                status, message = "unverifiable", "Control total or an observed category value is absent/nonnumeric; categories were not filled with zeros."
            elif expected < 0 or any(value < 0 for value in values):
                status, message = "failed", "Negative count encountered; the original value is retained."
            elif delta.copy_abs() > self.extraction.settings.category_tolerance:
                status, message = "failed", "Observed categories do not reconcile with the returned all total."
            else:
                status, message = "passed", "Categories reconcile within the configured absolute tolerance; values were not rounded or adjusted."
            results.append(status)
            self.event(row, "category_total", status, message, count_kind=kind, unit=unit,
                       phrase_total=expected, category_total=actual, difference=delta,
                       tolerance=self.extraction.settings.category_tolerance)
        return "passed" if results and all(status == "passed" for status in results) else "failed"

    def passed(self, row, kind):
        return self.states.get(row["record_id"], {}).get(kind) == "passed"

    def membership_checks(self):
        members = defaultdict(list)
        for row in self.job_rows.values():
            members[(row["analysis_uid"], row["language_part_id"])].append(row)
        for parent in self.extraction.tables["language_totals"].values():
            rows = members[(parent["analysis_uid"], parent["language_part_id"])]
            for kind, field in (("raw", "data"), ("discounted", "discountedData")):
                block = self.extraction.raw_counts.get(parent["record_id"], {}).get(field) or {}
                if block.get("available") is not True or not rows:
                    continue
                blocks = [self.extraction.raw_counts.get(r["record_id"], {}).get(field) or {} for r in rows]
                for unit in UNITS:
                    expected = number((block.get("all") or {}).get(unit))
                    if expected is None:
                        continue
                    if any(b.get("available") is not True for b in blocks):
                        continue  # Explicitly unavailable job data is a coverage gap, not a fabricated zero.
                    actual = total((b.get("all") or {}).get(unit) for b in blocks)
                    delta = difference(actual, expected)
                    status = "unverifiable" if delta is None else "failed" if delta.copy_abs() > self.extraction.settings.category_tolerance else "passed"
                    self.event(parent, "job_membership_total", status,
                               "Observed job totals reconciled with the language total; neither grain was added to the other.",
                               count_kind=kind, unit=unit, phrase_total=expected, category_total=actual,
                               difference=delta, tolerance=self.extraction.settings.category_tolerance)
                    if status != "passed":
                        self.states[parent["record_id"]][kind] = "failed"
                self.membership_categories(parent, kind, block, blocks)

    def membership_categories(self, parent, kind, parent_block, job_blocks):
        """Matching grand totals do not prove that their category split is correct.

        v1 jobs and v3 language parts have different NT bands. Compare NT at its
        origin total, preserving both original band sets without mapping them.
        """
        if any(block.get("available") is not True for block in job_blocks):
            return
        parent_categories = categories(parent_block)
        child_categories = [categories(block) for block in job_blocks]
        observed = parent_categories.keys() | {key for child in child_categories for key in child}
        keys = {(origin, None if origin == "nonTranslatablesMatches" else band) for origin, band in observed}
        for origin, band in sorted(keys, key=str):
            origin_total = origin == "nonTranslatablesMatches"
            groups = [[values for (o, b), values in mapping.items() if o == origin and (origin_total or b == band)]
                      for mapping in [parent_categories, *child_categories]]
            for unit in UNITS:
                if not any(unit in values for group in groups for values in group):
                    continue
                expected = total(values.get(unit) for values in groups[0])
                actual = total(total(values.get(unit) for values in group) for group in groups[1:])
                delta = difference(actual, expected)
                status = "unverifiable" if delta is None else "failed" if delta.copy_abs() > self.extraction.settings.category_tolerance else "passed"
                self.event(parent, "job_membership_category", status,
                           "Compare job categories with the language part; NT is checked by origin because API versions expose different bands.",
                           count_kind=kind, unit=unit, origin_key=origin, match_category=band,
                           resolution="origin_total" if origin_total else "origin_band",
                           phrase_total=expected, category_total=actual, difference=delta,
                           tolerance=self.extraction.settings.category_tolerance)
                if status != "passed":
                    self.states[parent["record_id"]][kind] = "failed"

    def summary(self):
        counts = {status: sum(check["status"] == status for check in self.checks)
                  for status in ("passed", "failed", "unverifiable", "unavailable", "warning")}
        return {"check_counts": counts, "valid": not (counts["failed"] or counts["unverifiable"]),
                "absolute_tolerance": self.extraction.settings.category_tolerance,
                "validated_units": list(UNITS), "values_adjusted": False}
