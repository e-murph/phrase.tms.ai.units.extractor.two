"""Project/job discovery, existing-analysis retrieval, coverage, and deduplication."""

from collections import defaultdict
from datetime import datetime, timezone
import re

from .client import ApiError
from .config import ConfigurationError, iso, timestamp
from .normalize import basis, measures, record_id, scheme_reference, totals
from .numeric import difference, total


TABLES = ("projects", "jobs", "analyses", "language_totals", "job_analyses",
          "selected_jobs", "selection", "measures", "net_rate_schemes", "coverage")


def uid(value):
    text = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ApiError("RESOURCE_ID", "TMS returned a missing or invalid resource identifier.")
    return text


def language(value):
    return value if isinstance(value, str) and value.strip() else None


def count_blocks(payload):
    raw, net = payload.get("data"), payload.get("discountedData")
    if ((raw is not None and not isinstance(raw, dict)) or (net is not None and not isinstance(net, dict)) or
            any(block and block.get("all") is not None and not isinstance(block["all"], dict) for block in (raw, net))):
        raise ApiError("COUNTS_SCHEMA", "Analysis count blocks are not objects; no totals were manufactured.")
    return raw or {}, net or {}


class Extraction:
    def __init__(self, client, product_mapping=None):
        self.client, self.settings = client, client.settings
        self.product_mapping = product_mapping or {}
        self.tables = {name: {} for name in TABLES}
        self.references = defaultdict(dict)
        self.details = {}
        self.job_counts = defaultdict(list)
        self.raw_counts = {}
        self.started_at = datetime.now(timezone.utc)
        self.identity = {}

    def put(self, table, key, row):
        row = self.client.safe(row)
        row.setdefault("record_id", record_id(self.settings.base_url, table, key))
        self.tables[table][key] = row
        return row

    def issue(self, code, message, impact="core", **context):
        key = (code, impact, tuple(sorted(context.items())))
        self.put("coverage", key, {**context, "code": code, "message": message, "impact": impact})

    def get(self, path, kind, key, context, impact="core"):
        try:
            payload = self.client.get(path)
            if not isinstance(payload, dict):
                raise ApiError("OBJECT_SCHEMA", "TMS returned an invalid resource object.")
            self.client.snapshot(kind, key, payload, path.split("/")[2])
            return payload
        except ApiError as error:
            if error.status == 401:
                raise
            self.issue(error.code, str(error), impact, **context)
            return None

    def collect(self, path, context, params=None, impact="core"):
        rows = []
        try:
            rows.extend(self.client.pages(path, params))
        except ApiError as error:
            if error.status == 401:
                raise
            self.issue(error.code, str(error), impact, **context)
        return rows

    def run(self):
        try:
            identity = self.client.get("/api2/v1/auth/whoAmI")
            if (not isinstance(identity, dict) or not isinstance(identity.get("organization"), dict) or
                    not identity["organization"].get("uid")):
                raise ApiError("IDENTITY_SCHEMA", "The REST identity has no organization; check credential type and region.")
            # Do not archive identity responses (they can include csrfToken and personal data).
            self.identity = {"organization_uid": identity["organization"].get("uid"),
                             "role": identity["user"].get("role") if isinstance(identity.get("user"), dict) else None}
            projects = []
            if self.settings.project_uids:
                projects = [{"uid": value} for value in self.settings.project_uids]
            else:
                for client_id in self.settings.client_ids or (None,):
                    params = {"includeArchived": str(self.settings.include_archived).lower(),
                              "sort": "ID", "order": "ASC"}
                    if client_id is not None:
                        params["clientId"] = client_id
                    projects.extend(self.collect("/api2/v1/projects", {"scope": "project_discovery"}, params))
            seen = set()
            for reference in projects:
                try:
                    project_uid = uid(reference.get("uid"))
                    if project_uid in seen:
                        continue
                    seen.add(project_uid)
                    self.project(project_uid, reference)
                except ApiError as error:
                    if error.status == 401:
                        raise
                    self.issue(error.code, str(error), scope="project")
        except ApiError as error:
            self.issue(error.code, str(error), scope="authentication_or_discovery")
        self.select()
        return self

    def project(self, project_uid, reference):
        context = {"project_uid": project_uid}
        prefix = f"/api2/v1/projects/{project_uid}"
        project = self.get(prefix, "project", project_uid, {**context, "scope": "project_metadata"}, "metadata") or reference
        if project.get("uid") != project_uid:
            raise ApiError("PROJECT_UID_MISMATCH", "Project metadata UID did not match the requested project.")
        if self.settings.project_names and project.get("name") not in self.settings.project_names:
            return
        client = project.get("client") or {}
        if not isinstance(client, dict):
            self.issue("CLIENT_SCHEMA", "Client metadata is malformed and was retained only in the source snapshot.", "metadata", **context)
            client = {}
        clean_client = dict(client)
        for field in ("name", "uid", "id"):
            value = client.get(field)
            valid = isinstance(value, str) if field != "id" else type(value) in {str, int}
            if isinstance(value, str) and not value.strip():
                clean_client[field] = None
            elif value is not None and not valid:
                self.issue("CLIENT_SCHEMA", "A client identity/display field is malformed; no identity was invented.", "metadata", **context)
                clean_client[field] = None
        client = clean_client
        project_name = project.get("name")
        if project_name is not None and not isinstance(project_name, str):
            self.issue("PROJECT_NAME_SCHEMA", "Project name is malformed; UID remains its identity.", "metadata", **context)
            project_name = None
        if self.settings.client_ids or self.settings.client_names:
            if not client:
                self.issue("CLIENT_FILTER_UNVERIFIED", "Client metadata is hidden; project excluded rather than bypassing the filter.", **context)
                return
            if self.settings.client_ids and str(client.get("id")) not in {str(x) for x in self.settings.client_ids}:
                return
            if self.settings.client_names and client.get("name") not in self.settings.client_names:
                return
        targets = project.get("targetLangs")
        if targets is not None and (not isinstance(targets, list) or any(language(t) is None for t in targets)):
            self.issue("PROJECT_LANGUAGE_SCHEMA", "Project target languages are malformed; original metadata remains in the source snapshot.",
                       "metadata", **context)
            targets = None
        project_row = self.put("projects", project_uid, {
            **context, "project_name": project_name, "client": client or None,
            "client_id": client.get("id"), "client_uid": client.get("uid"), "client_name": client.get("name"),
            "product_name": self.product_mapping.get(project_name),
            "source_language": language(project.get("sourceLang")), "target_languages": targets,
        })
        jobs = self.discover_jobs(project_uid)
        references = self.collect(f"/api2/v3/projects/{project_uid}/analyses", {**context, "scope": "project_analyses"},
                                  {"sort": "DATE_CREATED", "order": "desc",
                                   "onlyOwnerOrg": str(self.settings.only_owner_org).lower()})
        by_uid = {}
        for ref in references:
            if ref.get("uid"):
                by_uid[str(ref["uid"])] = ref
            else:
                self.issue("RESOURCE_ID", "Analysis discovery returned a reference without a UID.", **context)
        for job_uid in jobs:
            refs = self.collect(f"/api2/v3/projects/{project_uid}/jobs/{job_uid}/analyses",
                                {**context, "job_uid": job_uid, "scope": "job_analyses"})
            for ref in refs:
                if not ref.get("uid"):
                    self.issue("RESOURCE_ID", "Job analysis discovery returned a reference without a UID.", **context, job_uid=job_uid)
                    continue
                # Job discovery has no onlyOwnerOrg parameter. Apply ownership to the
                # project list only; do not allow the audit to bypass this scope.
                if self.settings.only_owner_org and str(ref["uid"]) not in by_uid:
                    continue
                analysis_uid = uid(ref["uid"])
                by_uid.setdefault(analysis_uid, ref)
                self.references[(project_uid, job_uid)][analysis_uid] = ref
        if not by_uid:
            self.issue("NO_ANALYSES", "No existing analyses were visible for this project; no zero counts were created.", "expected", **context)
        for analysis_uid, ref in by_uid.items():
            try:
                self.analysis(project_row, uid(analysis_uid), ref)
            except ApiError as error:
                if error.status == 401:
                    raise
                self.issue(error.code, str(error), **context, analysis_uid=analysis_uid)
        for job_uid in jobs:
            if not self.references[(project_uid, job_uid)]:
                self.issue("NO_JOB_ANALYSIS", "No existing analysis was visible for this job.", "expected", **context, job_uid=job_uid)

    def discover_jobs(self, project_uid):
        context = {"project_uid": project_uid}
        workflow = self.get(f"/api2/v2/projects/{project_uid}/workflowSteps", "workflow", project_uid,
                            {**context, "scope": "workflow_inventory"})
        steps = workflow.get("projectWorkflowSteps") if workflow else None
        if workflow is not None and not isinstance(steps, list):
            self.issue("WORKFLOW_SCHEMA", "Workflow response is missing its steps array; only default-level discovery is possible.", **context)
        levels = set()
        for step in steps or []:
            level = step.get("workflowLevel") if isinstance(step, dict) else None
            if type(level) is int and 1 <= level <= 15:
                levels.add(level)
            else:
                self.issue("WORKFLOW_SCHEMA", "A workflow step has an invalid workflowLevel.", **context)
        # With no configured workflow, the job endpoint's documented default is 1.
        found = set()
        for level in sorted(levels or {1}):
            rows = self.collect(f"/api2/v2/projects/{project_uid}/jobs", {**context, "scope": "job_inventory", "workflow_level": level},
                                {"workflowLevel": level, "count": "true"})
            for ref in rows:
                job_uid = uid(ref.get("uid"))
                if job_uid not in found:
                    self.job_metadata(project_uid, job_uid, ref, level)
                    found.add(job_uid)
                elif self.tables["jobs"][(project_uid, job_uid)]["workflow_level"] != level:
                    self.issue("JOB_WORKFLOW_DUPLICATE", "One job UID was returned in multiple workflow inventories; counts are excluded.",
                               **context, job_uid=job_uid, scope="job_metadata")
        return found

    def job_metadata(self, project_uid, job_uid, reference=None, level=None):
        key = (project_uid, job_uid)
        if key in self.tables["jobs"]:
            return self.tables["jobs"][key]
        detail = self.get(f"/api2/v1/projects/{project_uid}/jobs/{job_uid}", "job", ":".join(key),
                          {"project_uid": project_uid, "job_uid": job_uid, "scope": "job_metadata"}, "metadata")
        job = {**(reference or {}), **(detail or {})}
        context = {"project_uid": project_uid, "job_uid": job_uid, "scope": "job_metadata"}
        consistent = True

        def invalid(code, message):
            nonlocal consistent
            consistent = False
            self.issue(code, message, **context)

        if detail is not None and detail.get("uid") != job_uid:
            invalid("JOB_UID_MISMATCH", "Returned job UID differs from the requested UID; counts are excluded.")
        owner = job.get("project")
        if owner is not None and (not isinstance(owner, dict) or owner.get("uid") != project_uid):
            invalid("JOB_PROJECT_MISMATCH", "Returned job project differs from the requested project; counts are excluded.")
        workflow_level = job.get("workflowLevel", level)
        if type(workflow_level) is not int or not 1 <= workflow_level <= 15:
            invalid("JOB_WORKFLOW_SCHEMA", "Job workflow level is missing or malformed; workflow attribution is unverified.")
            workflow_level = level
        if level is not None and workflow_level != level:
            invalid("JOB_WORKFLOW_MISMATCH", "Job detail disagrees with the inventory workflow; counts are excluded.")
            workflow_level = level  # Retain the inventoried population, with invalid counts.
        step = job.get("workflowStep")
        if step is not None and (not isinstance(step, dict) or
                (step.get("workflowLevel") is not None and step.get("workflowLevel") != workflow_level)):
            invalid("JOB_WORKFLOW_MISMATCH", "Job workflow-step metadata is malformed or inconsistent; counts are excluded.")
            step = None
        source, target = language(job.get("sourceLang")), language(job.get("targetLang"))
        if any(job.get(k) is not None and language(job[k]) is None for k in ("sourceLang", "targetLang")):
            invalid("JOB_LANGUAGE_SCHEMA", "Job language metadata is malformed; no language was guessed.")
        lineage = job.get("jobReference")
        if lineage is not None and (not isinstance(lineage, dict) or
                (lineage.get("uid") is not None and not isinstance(lineage["uid"], str))):
            invalid("JOB_REFERENCE_SCHEMA", "Job lineage metadata is malformed; split-job overlaps cannot be verified.")
            lineage = None
        return self.put("jobs", key, {
            "project_uid": project_uid, "job_uid": job_uid, "filename": job.get("filename"),
            "source_job_uid": (lineage or {}).get("uid"),
            "source_language": source, "target_language": target,
            "workflow_level": workflow_level, "workflow_step": step,
            "job_created_at": job.get("dateCreated"), "source_updated_at": job.get("updateSourceDate"),
            "target_updated_at": job.get("updateTargetDate"), "begin_index": job.get("beginIndex"),
            "end_index": job.get("endIndex"), "continuous": job.get("continuous"),
            "is_parent_job_split": job.get("isParentJobSplit"), "split": job.get("split"),
            "metadata_available": detail is not None, "metadata_consistent": consistent,
        })

    def analysis(self, project, analysis_uid, reference):
        project_uid = project["project_uid"]
        context = {"project_uid": project_uid, "analysis_uid": analysis_uid}
        if analysis_uid not in self.details:
            self.details[analysis_uid] = self.get(f"/api2/v3/analyses/{analysis_uid}", "analysis", analysis_uid, context)
        detail = self.details[analysis_uid]
        body = {**reference, **(detail or {})}
        if detail is not None and detail.get("uid") != analysis_uid:
            raise ApiError("ANALYSIS_UID_MISMATCH", "Analysis metadata UID did not match the requested analysis.")
        owner = body.get("project")
        if owner is not None and (not isinstance(owner, dict) or owner.get("uid", project_uid) != project_uid):
            raise ApiError("PROJECT_MISMATCH", "Analysis project metadata is malformed or belongs to a different project; it was not counted.")
        settings = body.get("settings") or {}
        if not isinstance(settings, dict):
            raise ApiError("SETTINGS_SCHEMA", "Analysis settings are not an object; count basis is unknown.")
        try:
            in_period = self.settings.in_period(body.get("dateCreated"))
        except ConfigurationError:
            in_period = None
            self.issue("ANALYSIS_DATE_UNKNOWN", "Analysis date is missing/invalid; period and selection are unverified.", **context)
        updated_field = next((k for k in ("dateUpdated", "dateLastUpdated", "lastModifiedDate") if k in body), None)
        kind, import_status = body.get("type"), body.get("importStatus")
        if kind is not None and not isinstance(kind, str):
            self.issue("ANALYSIS_TYPE_SCHEMA", "Analysis type is malformed; it was not guessed.", **context)
            kind = None
        if import_status is not None and not isinstance(import_status, dict):
            self.issue("ANALYSIS_STATUS_SCHEMA", "Analysis readiness metadata is malformed; it was not considered ready.", **context)
            import_status = None
        row = self.put("analyses", analysis_uid, {
            **{k: v for k, v in project.items() if k != "record_id"}, **context,
            "analysis_name": body.get("name"), "analysis_type": kind,
            "analysis_created_at": body.get("dateCreated"),
            "analysis_updated_at": body.get(updated_field) if updated_field else None,
            "analysis_updated_at_field": updated_field, "analysis_inner_id": body.get("innerId"),
            "provider": body.get("provider"), "net_rate_scheme": body.get("netRateScheme"),
            "settings": settings, "settings_fingerprint": record_id(settings), "count_basis": basis(settings),
            "outdated": body.get("outdated"), "import_status": import_status,
            "warnings": body.get("pureWarnings"), "in_period": in_period,
            "retrieval_status": "retrieved" if detail is not None else "inaccessible",
            "endpoint_version": "v3",
        })
        if detail is None or in_period is False:
            return
        scheme = body.get("netRateScheme")
        has_scheme = scheme_reference(scheme)
        if scheme:
            try:
                if not isinstance(scheme, dict):
                    raise ApiError("SCHEME_SCHEMA", "Associated net-rate metadata is malformed.")
                self.scheme(uid(scheme.get("uid")))
            except ApiError as error:
                if error.status == 401:
                    raise
                self.issue(error.code, str(error), "metadata", **context)
        if self.settings.download_json:
            try:
                download = self.client.get(f"/api2/v1/analyses/{analysis_uid}/download", {"format": "JSON"})
                if not isinstance(download, dict) or not isinstance(download.get("analyseLanguageParts"), list):
                    raise ApiError("DOWNLOAD_SCHEMA", "Native JSON download has an unexpected format; it was not joined to REST counts.")
                self.client.snapshot("native_download", analysis_uid, download, "v1-export")
            except ApiError as error:
                if error.status == 401:
                    raise
                self.issue(error.code, str(error), "optional", **context, scope="native_download")
        parts = body.get("analyseLanguageParts")
        if not isinstance(parts, list):
            self.issue("ANALYSIS_SCHEMA", "Analysis is missing its language-parts array.", **context)
            return
        if not parts:
            self.issue("ANALYSIS_EMPTY", "Analysis has no language results; it may still be running.", **context)
        part_ids = set()
        for part in parts:
            if not isinstance(part, dict):
                self.issue("ANALYSIS_SCHEMA", "Invalid analysis language part.", **context)
                continue
            try:
                part_id = uid(part.get("id"))
                if not part_id.isdecimal():
                    raise ApiError("LANGUAGE_PART_ID", "Analysis language part IDs must be numeric.")
                if part_id in part_ids:
                    self.issue("DUPLICATE_LANGUAGE_PART", "Analysis repeated a language-part ID; ambiguous counts were excluded and original parts retained.",
                               **context, language_part_id=part_id, scope="analysis_membership")
                    continue
                part_ids.add(part_id)
                raw, net = count_blocks(part)
            except ApiError as error:
                self.issue(error.code, str(error), **context)
                continue
            part_context = {**row, "record_id": record_id(self.settings.base_url, analysis_uid, part_id),
                            "language_part_id": part_id, "source_language": language(part.get("sourceLang")),
                            "target_language": language(part.get("targetLang")), "translation_memories": part.get("transMemories"),
                            "language_raw_estimate": raw.get("estimate"),
                            "language_raw_available": raw.get("available"),
                            "language_discounted_estimate": net.get("estimate"),
                            "language_discounted_available": net.get("available"),
                            "grain": "analysis_language", "selected": False}
            language_row = self.add_counts("language_totals", (analysis_uid, part_id), part_context, part, has_scheme)
            members = self.collect(f"/api2/v1/analyses/{analysis_uid}/analyseLanguageParts/{part_id}/jobs",
                                   {**context, "language_part_id": part_id, "scope": "analysis_membership"})
            embedded_jobs = part.get("jobs")
            if embedded_jobs is not None and (not isinstance(embedded_jobs, list) or
                    any(not isinstance(j, dict) or not j.get("uid") for j in embedded_jobs)):
                self.issue("MEMBERSHIP_SCHEMA", "Embedded job references are malformed; paginated results remain inspectable but attribution is unverified.",
                           **context, language_part_id=part_id, scope="analysis_membership")
                embedded_jobs = []
            embedded = {str(j["uid"]): j for j in embedded_jobs or []}
            seen = set()
            member_rows = []
            for member in members:
                try:
                    job_uid = uid(member.get("uid"))
                except ApiError as error:
                    self.issue(error.code, str(error), **context, language_part_id=part_id)
                    continue
                if job_uid in seen:
                    continue
                seen.add(job_uid)
                job = self.job_metadata(project_uid, job_uid, embedded.get(job_uid))
                self.references[(project_uid, job_uid)][analysis_uid] = body
                language_mismatch = any(job.get(k) is not None and job[k] != part_context[k]
                                        for k in ("source_language", "target_language"))
                if language_mismatch or not part_context["source_language"] or not part_context["target_language"]:
                    self.issue("LANGUAGE_METADATA", "Job and analysis languages are missing or inconsistent; selected counts cannot be attributed safely.",
                               **context, job_uid=job_uid, language_part_id=part_id)
                job_context = {**part_context, **{k: v for k, v in job.items() if k not in
                               {"record_id", "source_language", "target_language"}},
                               "record_id": record_id(self.settings.base_url, analysis_uid, part_id, job_uid),
                               "source_job_uid": job.get("source_job_uid") or embedded.get(job_uid, {}).get("jobUid"),
                               "filename": member.get("filename", job.get("filename")),
                               "grain": "analysis_job", "endpoint_version": "v1",
                               "language_metadata_mismatch": language_mismatch,
                               "detail_limitations": "v1 NT bands differ from v3; no per-job estimate flag is documented"}
                try:
                    count_row = self.add_counts("job_analyses", (analysis_uid, part_id, job_uid), job_context, member, has_scheme)
                except ApiError as error:
                    self.issue(error.code, str(error), **context, job_uid=job_uid, language_part_id=part_id)
                    continue
                self.job_counts[(project_uid, job_uid, analysis_uid)].append(count_row)
                member_rows.append(count_row)
            if not set(embedded).issubset(seen):
                self.issue("MEMBERSHIP_MISMATCH", "Some embedded jobs were absent from paginated membership; coverage is incomplete.",
                           **context, language_part_id=part_id)
            if not seen and embedded:
                self.issue("MEMBERSHIP_EMPTY", "Language result references jobs but paginated membership was empty.", **context, language_part_id=part_id)
            for field in ("gross_words", "discounted_words"):
                expected = language_row[field]
                values = [r[field] for r in member_rows]
                if expected is not None and values and all(v is not None for v in values):
                    observed = total(values)
                    if difference(observed, expected).copy_abs() > self.settings.category_tolerance:
                        self.issue("TOTAL_MISMATCH", "Job word counts do not reconcile with the language total; inspect both preserved grains.",
                                   **context, language_part_id=part_id, field=field)
                elif expected is not None and expected > 0 and not values:
                    self.issue("MEMBERSHIP_MISSING", "Positive language totals have no job counts; selection coverage is incomplete.",
                               **context, language_part_id=part_id, field=field)

    def add_counts(self, table, key, context, payload, has_scheme):
        raw, net = count_blocks(payload)
        row = self.put(table, key, {**context, **totals(raw, net, has_scheme)})
        if context.get("language_raw_estimate") is True or context.get("language_discounted_estimate") is True:
            row.update(word_savings=None, word_savings_percent=None)
        self.raw_counts[row["record_id"]] = self.client.safe(payload)
        row["total_source_words"] = row["gross_words"] if row["count_basis"] == "source" else None
        if raw.get("available") is True and row["gross_words"] is None:
            self.issue("COUNTS_INCOMPLETE", "Raw counts are marked available but total words are missing or nonnumeric.",
                       analysis_uid=row["analysis_uid"], language_part_id=row["language_part_id"], grain=row["grain"])
        for measure in measures(context, raw, net, has_scheme):
            self.put("measures", measure["record_id"], measure)
        if row["word_savings"] is not None and row["word_savings"] < 0:
            self.issue("NEGATIVE_SAVINGS", "Returned net words exceed gross words; values are retained for review.",
                       "metadata", analysis_uid=row["analysis_uid"], language_part_id=row["language_part_id"])
        return row

    def scheme(self, scheme_uid):
        if scheme_uid in self.tables["net_rate_schemes"]:
            return
        context = {"net_rate_scheme_uid": scheme_uid, "scope": "scheme_metadata"}
        payload = self.get(f"/api2/v2/netRateSchemes/{scheme_uid}", "scheme", scheme_uid, context, "metadata")
        rates = []
        if payload is not None:
            steps = self.collect(f"/api2/v1/netRateSchemes/{scheme_uid}/workflowStepNetSchemes", context, impact="metadata")
            for step in steps:
                try:
                    step_id = uid(step.get("id"))
                except ApiError as error:
                    self.issue(error.code, str(error), "metadata", **context)
                    continue
                value = self.get(f"/api2/v1/netRateSchemes/{scheme_uid}/workflowStepNetSchemes/{step_id}",
                                 "workflow_rate", f"{scheme_uid}:{step_id}", context, "metadata")
                rates.append(value or step)
        self.put("net_rate_schemes", scheme_uid, {"net_rate_scheme_uid": scheme_uid,
                 "metadata_available": payload is not None, "scheme": payload, "workflow_rates": rates})

    def select(self):
        selected = set()
        for (project_uid, job_uid), refs in sorted(self.references.items()):
            groups = defaultdict(list)
            unresolved = False
            for analysis_uid, reference in refs.items():
                analysis = self.tables["analyses"].get(analysis_uid)
                if analysis is None:
                    self.issue("ANALYSIS_NOT_RETRIEVED", "Discovered analysis has no usable metadata; job selection is incomplete.",
                               project_uid=project_uid, job_uid=job_uid, analysis_uid=analysis_uid)
                    unresolved = True
                    continue
                kind = analysis.get("analysis_type")
                if analysis["in_period"] is False:
                    continue
                if kind is None:
                    unresolved = True
                    self.issue("ANALYSIS_TYPE_UNKNOWN", "An inaccessible or malformed analysis has an unknown type; job selection is unverified.",
                               project_uid=project_uid, job_uid=job_uid, analysis_uid=analysis_uid)
                    continue
                if kind not in self.settings.summary_types:
                    continue
                if analysis["count_basis"] not in {self.settings.count_basis, "unknown"}:
                    continue
                groups[kind].append(analysis)
            for kind, candidates in groups.items():
                context = {"project_uid": project_uid, "job_uid": job_uid, "analysis_type": kind}
                try:
                    newest = max(candidates, key=lambda a: (timestamp(a["analysis_created_at"]), a["analysis_uid"]))
                except ConfigurationError:
                    self.issue("SELECTION_DATE_UNKNOWN", "An analysis date is unknown; an older candidate was not substituted.", **context)
                    self.put("selection", (project_uid, job_uid, kind), {**context, "selected": False, "reason": "unknown_date"})
                    continue
                analysis_uid = newest["analysis_uid"]
                rows = self.job_counts[(project_uid, job_uid, analysis_uid)]
                reason = None
                if unresolved:
                    reason = "unresolved_analysis_reference"
                elif newest["retrieval_status"] != "retrieved":
                    reason = "latest_inaccessible"
                elif newest["count_basis"] == "unknown":
                    reason = "unknown_count_basis"
                elif newest.get("outdated") is not False:
                    reason = "outdated_or_unknown"
                elif (newest.get("import_status") or {}).get("status") != "OK":
                    reason = "not_ready"
                elif len(rows) != 1:
                    reason = "missing_or_ambiguous_job_counts"
                elif rows[0].get("metadata_consistent") is False:
                    reason = "job_metadata_inconsistent"
                elif (rows[0]["language_metadata_mismatch"] or not rows[0]["source_language"] or not rows[0]["target_language"]):
                    reason = "language_metadata_unverified"
                elif rows[0]["raw_available"] is not True or rows[0]["gross_words"] is None:
                    reason = "raw_counts_unavailable"
                elif rows[0]["language_raw_available"] is not True:
                    reason = "language_counts_unavailable"
                elif rows[0]["raw_estimate"] is True or rows[0]["language_raw_estimate"] is True:
                    reason = "estimated_counts"
                selection = {**context, "analysis_uid": analysis_uid, "selected": reason is None, "reason": reason or "latest_eligible"}
                self.put("selection", (project_uid, job_uid, kind), selection)
                if reason:
                    self.issue("SELECTION_EXCLUDED", "Latest candidate excluded: " + reason + "; no fallback counts were created.",
                               "expected", **context, analysis_uid=analysis_uid)
                    continue
                row = rows[0]
                row["selected"] = True
                self.put("selected_jobs", (project_uid, job_uid, kind), row.copy())
                selected.add((project_uid, job_uid, analysis_uid))
        # A parent and its split children may have distinct part UIDs but cover
        # the same source segments. Exclude proven overlaps, preserving inventory.
        lineages = defaultdict(list)
        for key, row in self.tables["selected_jobs"].items():
            begin, end = row.get("begin_index"), row.get("end_index")
            if ((row.get("is_parent_job_split") is True or row.get("split") is True) and
                    (not row.get("source_job_uid") or type(begin) is not int or type(end) is not int or not 0 <= begin <= end)):
                self.issue("SPLIT_RANGE_UNKNOWN", "A known split job lacks verifiable lineage/segment bounds; overlapping coverage cannot be ruled out.",
                           project_uid=row["project_uid"], job_uid=row["job_uid"], scope="job_metadata")
            if row.get("source_job_uid") and type(begin) is int and type(end) is int and 0 <= begin <= end:
                lineage = tuple(row.get(k) for k in ("project_uid", "source_job_uid", "workflow_level",
                                "source_language", "target_language", "count_basis", "analysis_type"))
                lineages[lineage].append((begin, end, key))
        overlaps = set()
        for entries in lineages.values():
            furthest, previous = -1, None
            for begin, end, key in sorted(entries):
                if begin <= furthest:
                    overlaps.update((previous, key))
                if end > furthest:
                    furthest, previous = end, key
        for key in overlaps:
            row = self.tables["selected_jobs"].pop(key)
            selected.discard((row["project_uid"], row["job_uid"], row["analysis_uid"]))
            self.tables["job_analyses"][(row["analysis_uid"], row["language_part_id"], row["job_uid"])]["selected"] = False
            self.tables["selection"][key].update(selected=False, reason="overlapping_job_parts")
            self.issue("OVERLAPPING_JOB_PARTS", "Source segment ranges overlap within one workflow/type; all ambiguous parts were excluded from selection.",
                       project_uid=row["project_uid"], job_uid=row["job_uid"], analysis_uid=row["analysis_uid"])
        for measure in self.tables["measures"].values():
            if measure.get("grain") == "analysis_job":
                measure["selected"] = (measure["project_uid"], measure["job_uid"], measure["analysis_uid"]) in selected

    def manifest(self):
        issues = self.tables["coverage"].values()
        core_complete = not any(i["impact"] == "core" for i in issues)
        metadata_complete = not any(i["impact"] == "metadata" for i in issues)
        return {"schema_version": 1, "extracted_at": iso(self.started_at), "finished_at": iso(datetime.now(timezone.utc)),
                "identity": self.identity, "configuration": self.settings.public(),
                "core_complete": core_complete, "metadata_complete": metadata_complete,
                "optional_complete": not any(i["impact"] == "optional" for i in issues),
                "status": "complete" if core_complete and metadata_complete else "partial",
                "record_counts": {name: len(rows) for name, rows in self.tables.items()},
                "request_count": self.client.request_count,
                "limitations": ["v1 job NT bands differ from v3 language totals; original bands preserved",
                                "per-job estimate flag and analysis update timestamp are not guaranteed by the API",
                                "selection is per analyzed job/workflow and type; never add inventory and selected totals",
                                "saved files are current observations, not immutable historical as-of states"]}
