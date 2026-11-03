"""Validated client-facing reports, separate from overlapping analysis inventories."""

from collections import defaultdict
from itertools import product

from .config import iso
from .normalize import record_id, scheme_reference
from .numeric import difference, number, percentage, total
from .validation import ORIGINS, Validation, categories, category_maps


ORIGIN_COLUMNS = {"transMemoryMatches": "tm", "repetitions": "repetition",
                  "machineTranslationMatches": "mt", "nonTranslatablesMatches": "non_translatable",
                  "internalFuzzyMatches": "internal_fuzzy"}

DETAIL_COLUMNS = ["client_name", "project_name", "project_uid", "analysis_uid", "analysis_name",
                  "analysis_date", "analysis_type", "job_uid", "filename", "workflow_step", "workflow_level",
                  "source_language", "target_language", "match_origin", "match_category", "origin_key",
                  "gross_words", "discounted_words", "saved_words", "gross_characters", "gross_segments",
                  "net_rate_scheme", "data_status", "raw_available", "discounted_available", "selected_for_summary"]
SUMMARY_COLUMNS = ["client_name", "project_name", "project_uid", "source_language", "target_language",
                   "language_pair", "scope_id", "workflow_level", "analysis_type", "count_basis", "period_start",
                   "period_end", "period_basis", "total_gross_words", "tm_words", "repetition_words", "mt_words",
                   "non_translatable_words", "internal_fuzzy_words", "discounted_words", "saved_words",
                   "leverage_percentage", "number_of_jobs_covered", "number_of_jobs_missing_valid_analyses",
                   "data_status", "gross_words_complete", "discounted_words_complete"]


def partial_sum(values):
    return total(value for value in values if number(value) is not None)


def merge_maps(children, field, strict):
    origins = {origin for child in children for origin in child.get(field, {})}
    result = {}
    for origin in sorted(origins):
        bands = {band for child in children for band in child.get(field, {}).get(origin, {})}
        result[origin] = {band: (total if strict else partial_sum)(
            child.get(field, {}).get(origin, {}).get(band) for child in children) for band in sorted(bands)}
    return result


def combine(children):
    """Same aggregation at both levels; clients consume project rows, not detail rows."""
    covered = sum(child["number_of_jobs_covered"] for child in children)
    missing = sum(child["known_jobs_missing_valid_analyses"] for child in children)
    known_jobs = sum(child["known_jobs_in_scope"] for child in children)
    inventory_complete = all(child["job_inventory_complete"] for child in children)
    raw_complete = bool(children) and inventory_complete and missing == 0 and covered > 0
    paired = sum(child["number_of_jobs_with_discounted_data"] for child in children)
    net_complete = raw_complete and paired == covered
    gross_known = partial_sum(child["known_gross_words"] for child in children)
    net_known = partial_sum(child["known_discounted_words"] for child in children)
    savings_known = partial_sum(child["known_saved_words"] for child in children)
    paired_gross = partial_sum(child["gross_words_with_discounted_data"] for child in children)
    result = {
        "number_of_jobs_covered": covered, "known_jobs_missing_valid_analyses": missing,
        "number_of_jobs_missing_valid_analyses": missing if inventory_complete else None,
        "known_jobs_in_scope": known_jobs, "number_of_jobs_in_scope": known_jobs if inventory_complete else None,
        "number_of_jobs_with_discounted_data": paired, "job_inventory_complete": inventory_complete,
        "gross_words_complete": raw_complete, "discounted_words_complete": net_complete,
        "total_gross_words": gross_known if raw_complete else None,
        "discounted_words": net_known if net_complete else None, "saved_words": savings_known if net_complete else None,
        "known_gross_words": gross_known, "known_discounted_words": net_known, "known_saved_words": savings_known,
        "gross_words_with_discounted_data": paired_gross,
        "leverage_percentage": percentage(savings_known, gross_known) if net_complete else None,
        "known_leverage_percentage": percentage(savings_known, paired_gross),
        "leverage_denominator_words": gross_known if net_complete else None,
        "known_leverage_denominator_words": paired_gross,
        "gross_category_totals": merge_maps(children, "gross_category_totals", True),
        "discounted_category_totals": merge_maps(children, "discounted_category_totals", True),
        "known_gross_category_totals": merge_maps(children, "known_gross_category_totals", False),
        "known_discounted_category_totals": merge_maps(children, "known_discounted_category_totals", False),
        "data_status": "no_jobs" if known_jobs == 0 and inventory_complete else
                       "partial_raw" if not raw_complete else "partial_discounted" if not net_complete else "complete",
    }
    for origin, prefix in ORIGIN_COLUMNS.items():
        for suffix in ("words", "discounted_words"):
            field = prefix + "_" + suffix
            result[field] = total(child.get(field) for child in children) if raw_complete and (suffix == "words" or net_complete) else None
            result["known_" + field] = partial_sum(child.get("known_" + field) for child in children)
    return result


class Reports:
    def __init__(self, extraction):
        self.extraction, self.settings = extraction, extraction.settings
        self.validation = Validation(extraction)
        self.core_issues = [issue for issue in extraction.tables["coverage"].values() if issue["impact"] == "core"]
        self.language_rows = {(r["analysis_uid"], r["language_part_id"]): r
                              for r in extraction.tables["language_totals"].values()}
        self.job_rows = list(self.validation.job_rows.values())
        self.raw = extraction.raw_counts
        self.eligible = {r["record_id"] for r in self.job_rows if self.eligible_job(r)}
        self.details = self.detailed()
        self.projects = self.project_summaries()
        self.clients = self.client_summaries()

    def scope(self, level, kind, count_basis=None):
        public = self.settings.public()
        fields = ("base_url", "period_start", "period_end", "period_basis", "project_uids", "project_names",
                  "client_ids", "client_names", "include_archived", "only_owner_org", "count_basis", "selection_policy", "category_tolerance")
        scope = {key: sorted(set(public[key])) if isinstance(public[key], (list, tuple)) else public[key] for key in fields}
        scope.update(organization_uid=self.extraction.identity.get("organization_uid"), workflow_level=level,
                     analysis_type=kind, report_scope_version=2)
        if count_basis is not None:
            scope["count_basis"] = count_basis
        return {**scope, "scope_id": record_id(scope)[:16]}

    def discovery_valid(self, row, kind="raw"):
        for issue in self.core_issues:
            if issue.get("project_uid") not in (None, row["project_uid"]):
                continue
            if issue.get("job_uid") not in (None, row.get("job_uid")):
                continue
            if issue.get("analysis_uid") not in (None, row.get("analysis_uid")):
                continue
            if issue.get("language_part_id") not in (None, row.get("language_part_id")):
                continue
            if issue.get("scope") in {"project_discovery", "project", "authentication_or_discovery", "project_analyses", "job_analyses", "analysis_membership", "job_metadata"}:
                return False
            if issue.get("code") in {"TOTAL_MISMATCH", "MEMBERSHIP_MISMATCH", "MEMBERSHIP_MISSING", "MEMBERSHIP_EMPTY"}:
                if issue.get("code") == "TOTAL_MISMATCH" and issue.get("field") == "discounted_words" and kind == "raw":
                    continue
                return False
        return True

    def eligible_job(self, row):
        parent = self.language_rows.get((row["analysis_uid"], row["language_part_id"]))
        return (row.get("selected") is True and row["workflow_level"] in self.settings.report_workflow_levels and
                row["analysis_type"] in self.settings.summary_types and row["count_basis"] == self.settings.count_basis and
                row["record_id"] not in self.validation.ambiguous_jobs and self.validation.passed(row, "raw") and
                parent is not None and self.validation.passed(parent, "raw") and self.discovery_valid(row))

    @staticmethod
    def has_scheme(row):
        return scheme_reference(row.get("net_rate_scheme"))

    def net_valid(self, row):
        parent = self.language_rows.get((row["analysis_uid"], row["language_part_id"]))
        return (self.has_scheme(row) and self.validation.passed(row, "discounted") and parent is not None and
                self.validation.passed(parent, "discounted") and self.discovery_valid(row, "discounted"))

    def detailed(self):
        rows = {}
        for job in self.job_rows:
            if job["record_id"] in self.validation.ambiguous_jobs:
                continue
            payload = self.raw.get(job["record_id"], {})
            raw_block, net_block = payload.get("data") or {}, payload.get("discountedData") or {}
            raw, net = categories(raw_block), categories(net_block)
            parent = self.language_rows.get((job["analysis_uid"], job["language_part_id"]))
            raw_valid = (self.validation.passed(job, "raw") and parent is not None and
                         self.validation.passed(parent, "raw") and self.discovery_valid(job))
            net_valid = self.net_valid(job)
            for origin, band in sorted(raw.keys() | net.keys(), key=str):
                gross, discounted = raw.get((origin, band), {}), net.get((origin, band), {})
                raw_available, net_available = raw_block.get("available"), net_block.get("available")
                g = gross.get("words") if raw_available is True else None
                n = discounted.get("words") if net_available is True and self.has_scheme(job) else None
                saved = difference(g, n) if raw_valid and net_valid else None
                scheme = job.get("net_rate_scheme") if isinstance(job.get("net_rate_scheme"), dict) else {}
                scope = self.scope(job.get("workflow_level"), job.get("analysis_type"), job.get("count_basis"))
                step = job.get("workflow_step")
                key = tuple(job.get(k) for k in ("project_uid", "analysis_uid", "job_uid", "source_language", "target_language")) + (origin, band)
                rows[key] = {
                    **{k: job.get(k) for k in ("client_name", "client_uid", "client_id", "project_name", "project_uid", "analysis_uid",
                        "analysis_name", "analysis_type", "job_uid", "filename", "workflow_step", "workflow_level", "source_language",
                        "target_language", "language_part_id", "count_basis", "settings_fingerprint", "endpoint_version")},
                    "analysis_date": job.get("analysis_created_at"), "match_origin": ORIGINS.get(origin, origin),
                    "workflow_step": step.get("name") if isinstance(step, dict) else step,
                    "workflow_step_metadata": step,
                    "scope_id": scope["scope_id"], "period_start": scope["period_start"], "period_end": scope["period_end"],
                    "period_basis": scope["period_basis"], "analysis_in_period": job.get("in_period"),
                    "origin_key": origin, "match_category": band or origin, "gross_words": g,
                    "discounted_words": n, "saved_words": saved,
                    "gross_characters": gross.get("characters") if raw_available is True else None,
                    "gross_segments": gross.get("segments") if raw_available is True else None,
                    "discounted_characters": discounted.get("characters") if net_available is True and self.has_scheme(job) else None,
                    "discounted_segments": discounted.get("segments") if net_available is True and self.has_scheme(job) else None,
                    "net_rate_scheme": scheme.get("name"), "net_rate_scheme_uid": scheme.get("uid"),
                    "raw_available": raw_available, "discounted_available": net_available,
                    **{k: job.get(k) for k in ("raw_estimate", "discounted_estimate", "language_raw_estimate", "language_discounted_estimate")},
                    "raw_category_present": (origin, band) in raw, "discounted_category_present": (origin, band) in net,
                    "raw_validation": self.validation.states.get(job["record_id"], {}).get("raw"),
                    "discounted_validation": self.validation.states.get(job["record_id"], {}).get("discounted"),
                    "language_raw_validation": self.validation.states.get(parent["record_id"], {}).get("raw") if parent else None,
                    "language_discounted_validation": self.validation.states.get(parent["record_id"], {}).get("discounted") if parent else None,
                    "data_status": "raw_unavailable" if raw_available is not True else "raw_validation_failed" if not raw_valid else
                                   "raw_category_not_returned" if (origin, band) not in raw else
                                   "discounted_unavailable" if not self.has_scheme(job) or net_available is not True else
                                   "discounted_estimated" if job.get("discounted_estimate") is True or job.get("language_discounted_estimate") is True else
                                   "discounted_category_not_returned" if (origin, band) not in net else
                                   "discounted_validation_failed" if not net_valid else "complete",
                    "selected_for_summary": job["record_id"] in self.eligible,
                    "raw_metrics": gross, "discounted_metrics": discounted,
                    "record_id": record_id("detailed", key),
                }
        return sorted(rows.values(), key=lambda r: r["record_id"])

    def job_stats(self, row):
        valid = row is not None and row["record_id"] in self.eligible
        paired = valid and self.net_valid(row)
        payload = self.raw.get(row["record_id"], {}) if row else {}
        raw_block, net_block = payload.get("data") or {}, payload.get("discountedData") or {}
        gross = (raw_block.get("all") or {}).get("words") if valid else None
        net = (net_block.get("all") or {}).get("words") if paired else None
        saved = difference(gross, net) if paired else None
        raw_map, net_map = category_maps(raw_block) if valid else {}, category_maps(net_block) if paired else {}
        stat = {"number_of_jobs_covered": int(valid), "known_jobs_missing_valid_analyses": int(not valid),
                "known_jobs_in_scope": 1, "number_of_jobs_with_discounted_data": int(paired), "job_inventory_complete": True,
                "known_gross_words": gross, "known_discounted_words": net, "known_saved_words": saved,
                "gross_words_with_discounted_data": gross if paired else None,
                "gross_category_totals": raw_map, "discounted_category_totals": net_map,
                "known_gross_category_totals": raw_map, "known_discounted_category_totals": net_map}
        for origin, prefix in ORIGIN_COLUMNS.items():
            for suffix, mapping in (("words", raw_map), ("discounted_words", net_map)):
                value = total(mapping.get(origin, {}).values())
                stat[prefix + "_" + suffix] = value
                stat["known_" + prefix + "_" + suffix] = value
        return stat

    def inventory_complete(self, project_uid, level):
        for issue in self.core_issues:
            if (issue.get("project_uid") in (None, project_uid) and
                    issue.get("workflow_level") in (None, level) and issue.get("scope") in
                    {"project_discovery", "project", "authentication_or_discovery", "workflow_inventory", "job_inventory"}):
                return False
        return True

    def inventory(self):
        jobs = {key: dict(row) for key, row in self.extraction.tables["jobs"].items()}
        alternatives = defaultdict(list)
        for row in self.job_rows:
            key = (row["project_uid"], row["job_uid"])
            jobs.setdefault(key, dict(row))
            alternatives[key].append(row)
        for key, job in jobs.items():
            for field in ("source_language", "target_language", "workflow_level"):
                values = {row.get(field) for row in alternatives[key] if row.get(field) is not None}
                if job.get(field) is None and len(values) == 1:
                    job[field] = values.pop()
        return jobs

    def project_summaries(self):
        inventory = self.inventory()
        selected = {(r["project_uid"], r["job_uid"], r["analysis_type"]): r for r in self.job_rows if r.get("selected")}
        rows = []
        for project_uid, project_row in self.extraction.tables["projects"].items():
            project_jobs = [j for (p, _), j in inventory.items() if p == project_uid]
            for level, kind in product(self.settings.report_workflow_levels, sorted(set(self.settings.summary_types))):
                groups = defaultdict(list)
                for job in project_jobs:
                    if job.get("workflow_level") == level:
                        groups[(job.get("source_language"), job.get("target_language"))].append(job)
                for target in project_row.get("target_languages") or []:
                    if not any(existing_target == target for _, existing_target in groups):
                        groups.setdefault((project_row.get("source_language"), target), [])
                if not groups:
                    groups[(project_row.get("source_language"), None)] = []
                scope = self.scope(level, kind)
                for (source, target), jobs in groups.items():
                    stats = [self.job_stats(selected.get((project_uid, job["job_uid"], kind))) for job in jobs]
                    summary = combine(stats)
                    complete = (self.inventory_complete(project_uid, level) and
                                all(j.get("workflow_level") is not None for j in project_jobs) and
                                source is not None and target is not None)
                    if not complete:
                        summary.update(job_inventory_complete=False, number_of_jobs_in_scope=None,
                                       number_of_jobs_missing_valid_analyses=None, gross_words_complete=False,
                                       discounted_words_complete=False, total_gross_words=None, discounted_words=None,
                                       saved_words=None, leverage_percentage=None, leverage_denominator_words=None,
                                       data_status="inventory_incomplete")
                        for prefix in ORIGIN_COLUMNS.values():
                            summary[prefix + "_words"] = summary[prefix + "_discounted_words"] = None
                        summary["gross_category_totals"] = {o: {b: None for b in bands} for o, bands in summary["gross_category_totals"].items()}
                        summary["discounted_category_totals"] = {o: {b: None for b in bands} for o, bands in summary["discounted_category_totals"].items()}
                    settings = sorted({row["settings_fingerprint"] for job in jobs
                                       for row in [selected.get((project_uid, job["job_uid"], kind))] if row is not None})
                    schemes = {str(s.get("uid") or s.get("id")): s for job in jobs
                               for row in [selected.get((project_uid, job["job_uid"], kind))] if row is not None
                               for s in [row.get("net_rate_scheme")] if isinstance(s, dict)}
                    rows.append({**{k: project_row.get(k) for k in ("client_name", "client_id", "client_uid", "project_name", "project_uid")},
                                 **{k: scope[k] for k in ("scope_id", "workflow_level", "analysis_type", "count_basis", "period_start", "period_end", "period_basis")},
                                 "source_language": source, "target_language": target,
                                 "language_pair": source + ">" + target if source and target else None,
                                 "analysis_settings_fingerprints": settings, "mixed_analysis_settings": len(settings) > 1,
                                 "net_rate_schemes_observed": [schemes[key] for key in sorted(schemes)],
                                 **summary, "record_id": record_id("project", project_uid, source, target, scope["scope_id"])})
        return sorted(rows, key=lambda r: r["record_id"])

    def client_summaries(self):
        aliases = defaultdict(set)
        for row in self.projects:
            if row.get("client_id") and row.get("client_uid"):
                aliases[str(row["client_id"])].add(row["client_uid"])
        groups = defaultdict(list)
        for row in self.projects:
            client_id = str(row["client_id"]) if row.get("client_id") is not None else None
            resolved = aliases.get(client_id, set())
            identity = "uid:" + str(row["client_uid"]) if row.get("client_uid") else "uid:" + str(next(iter(resolved))) if len(resolved) == 1 else \
                       "id:" + client_id if client_id is not None and len(resolved) <= 1 else "unresolved_project:" + row["project_uid"]
            key = (identity, row["source_language"], row["target_language"], row["scope_id"])
            groups[key].append(row)
        result = []
        for key, children in groups.items():
            first = children[0]
            stats = combine(children)
            fingerprints = sorted({f for row in children for f in row["analysis_settings_fingerprints"]})
            names = sorted({r["client_name"] for r in children if r.get("client_name")})
            client_ids = sorted({str(r["client_id"]) for r in children if r.get("client_id") is not None})
            client_uids = sorted({r["client_uid"] for r in children if r.get("client_uid")})
            schemes = {str(s.get("uid") or s.get("id")): s for child in children for s in child["net_rate_schemes_observed"]}
            result.append({**{k: first.get(k) for k in ("client_id", "client_uid", "source_language", "target_language", "language_pair",
                          "scope_id", "workflow_level", "analysis_type", "count_basis", "period_start", "period_end", "period_basis")},
                           "client_name": names[0] if len(names) == 1 else None, "client_names_observed": names,
                           "client_id": client_ids[0] if len(client_ids) == 1 else None,
                           "client_uid": client_uids[0] if len(client_uids) == 1 else None,
                           "client_identity": key[0], "client_identity_resolved": not key[0].startswith("unresolved"),
                           "number_of_projects": len(children), "project_uids": sorted(r["project_uid"] for r in children),
                           "analysis_settings_fingerprints": fingerprints, "mixed_analysis_settings": len(fingerprints) > 1,
                           "net_rate_schemes_observed": [schemes[key] for key in sorted(schemes)],
                           **stats, "record_id": record_id("client", key)})
        return sorted(result, key=lambda r: r["record_id"])

    def structured(self):
        analyses = []
        known_record_ids = {row["record_id"] for row in self.job_rows}
        native = {s["key"]: s["payload"] for s in self.extraction.client.snapshots.values() if s["kind"] == "native_download"}
        job_index = defaultdict(list)
        for row in self.job_rows:
            job_index[(row["analysis_uid"], row["language_part_id"])].append({
                "metadata": row, "data": self.raw.get(row["record_id"], {}).get("data"),
                "discountedData": self.raw.get(row["record_id"], {}).get("discountedData"),
                "validation": self.validation.states.get(row["record_id"]),
                "selected_for_reporting": row["record_id"] in self.eligible})
        for row in sorted(self.extraction.tables["analyses"].values(), key=lambda r: r["analysis_uid"]):
            analysis_uid = row["analysis_uid"]
            payload = self.extraction.details.get(analysis_uid) or {}
            language_parts = []
            returned_parts = payload.get("analyseLanguageParts")
            for part in returned_parts if isinstance(returned_parts, list) else []:
                if not isinstance(part, dict):
                    continue
                part_id = str(part.get("id"))
                normalized = self.language_rows.get((analysis_uid, part_id))
                language_parts.append({"metadata": {k: v for k, v in part.items() if k not in {"data", "discountedData", "jobs"}},
                                       "data": part.get("data"), "discountedData": part.get("discountedData"),
                                       "embedded_job_references": part.get("jobs"),
                                       "jobs": sorted(job_index[(analysis_uid, part_id)], key=lambda r: r["metadata"]["job_uid"]),
                                       "validation": self.validation.states.get(normalized["record_id"]) if normalized else None})
            analyses.append({"metadata": row, "returned_metadata": {k: v for k, v in payload.items() if k != "analyseLanguageParts"},
                             "language_parts": language_parts,
                             "unparsed_language_parts": returned_parts if not isinstance(returned_parts, list) else
                                 [part for part in returned_parts if not isinstance(part, dict)],
                             "native_download": native.get(analysis_uid)})
        return {"schema_version": 2, "extracted_at": iso(self.extraction.started_at),
                "configuration": self.settings.public(), "identity": self.extraction.identity,
                "project_inventory": sorted(self.extraction.tables["projects"].values(), key=lambda r: r["record_id"]),
                "job_inventory": sorted(self.extraction.tables["jobs"].values(), key=lambda r: r["record_id"]),
                "selection_decisions": sorted(self.extraction.tables["selection"].values(), key=lambda r: r["record_id"]),
                "net_rate_schemes": sorted(self.extraction.tables["net_rate_schemes"].values(), key=lambda r: r["record_id"]),
                "analysis_data": analyses, "project_summary": self.projects, "client_summary": self.clients,
                "unresolved_job_records": [
                    {"metadata": row, "returned_data": self.raw.get(row["record_id"]), "selected_for_reporting": False}
                    for row in self.extraction.tables["job_analyses"].values()
                    if row["record_id"] not in known_record_ids],
                "validation": self.validation.checks, "coverage": list(self.extraction.tables["coverage"].values()),
                "field_provenance": {"data": "Phrase raw categories", "discountedData": "Phrase discounted categories",
                                     "saved_words": "local gross minus valid discounted words",
                                     "leverage_percentage": "100 * complete saved words / complete gross words",
                                     "known_leverage_percentage": "100 * known saved words / gross words of the same valid discounted subset"}}
