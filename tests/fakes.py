"""Synthetic Phrase contracts only. No user payloads, credentials, or live HTTP."""

from copy import deepcopy
from urllib.parse import urlsplit

import requests

from leverage.numeric import dumps


def response(status, payload=None, headers=None, content=None):
    result = requests.Response()
    result.status_code = status
    result._content = content if content is not None else dumps(payload).encode("utf-8")
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


def page(rows, params):
    size, index = params.get("pageSize", 50), params.get("pageNumber", 0)
    return {"content": rows[index * size:(index + 1) * size], "pageNumber": index,
            "pageSize": size, "totalElements": len(rows), "totalPages": (len(rows) + size - 1) // size}


def counts(words, net=False, version=3):
    factor = .5 if net else 1
    def c(value):
        return {"words": value * factor, "characters": value * factor * 5,
                "segments": value * factor / 10, "normalizedPages": value * factor / 360,
                "percent": value, "editingTime": 0}
    return {"available": True, **({"estimate": False} if version == 3 else {}),
            "all": c(words), "repetitions": c(words / 10),
            "transMemoryMatches": {"match100": c(words / 5), "match0": c(words * .3), "match101": c(0)},
            "machineTranslationMatches": {"match95": c(words / 10)},
            "internalFuzzyMatches": {"match75": c(words / 10)},
            "nonTranslatablesMatches": {"match95" if version == 3 else "match99": c(words / 10)},
            "futureMatchCategory": {"match97": c(words / 10)}}


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []
        self.closed = False

    def get(self, url, **kwargs):
        path = urlsplit(url).path.removeprefix("/web")
        self.calls.append((path, deepcopy(kwargs)))
        return self.handler(path, kwargs.get("params", {}))

    def close(self):
        self.closed = True


class PhraseFixture:
    def __init__(self):
        self.projects = {"P1": {"uid": "P1", "name": "Product German", "client": {"id": "7", "uid": "C7", "name": "Client Seven"}},
                         "P2": {"uid": "P2", "name": "No analyses", "client": {"id": "8", "name": "Other client"}}}
        self.jobs = {
            "J1": self.job("J1", "de", 1), "J2": self.job("J2", "de", 2),
            "J3": self.job("J3", "fr", 1), "J4": self.job("J4", "ja", 1),
            "J5": self.job("J5", "de", 1, "P2"),
        }
        self.analyses = {
            "A1": self.analysis("A1", "2026-01-02T00:00:00Z", [("1", "de", {"J1": 80, "J2": 120})]),
            "A2": self.analysis("A2", "2026-02-02T00:00:00Z", [("2", "de", {"J1": 100}), ("3", "fr", {"J3": 200})]),
            "A3": self.analysis("A3", "2026-03-02T00:00:00Z", [("4", "de", {"J2": 120})], kind="PostAnalyse"),
            "A4": self.analysis("A4", "2026-03-03T00:00:00Z", [("5", "ja", {"J4": 0})], scheme=False),
        }
        self.overrides = {}
        self.extra_job_refs = {}

    @staticmethod
    def job(job_uid, target, level, project_uid="P1"):
        return {"uid": job_uid, "filename": "same-file.txt", "sourceLang": "en", "targetLang": target,
                "workflowLevel": level, "workflowStep": {"id": str(level), "name": "Step " + str(level), "workflowLevel": level},
                "jobReference": {"uid": "SOURCE12" if job_uid in {"J1", "J2"} else "SOURCE" + job_uid},
                "dateCreated": "2020-01-01T00:00:00Z", "updateSourceDate": None,
                "project": {"uid": project_uid}, "beginIndex": 0, "endIndex": 10}

    @staticmethod
    def analysis(analysis_uid, created, parts, kind="PreAnalyse", scheme=True):
        body = {"uid": analysis_uid, "name": "Analysis " + analysis_uid, "type": kind,
                "dateCreated": created, "outdated": False, "importStatus": {"status": "OK"},
                "project": {"uid": "P1"}, "settings": {"type": kind, "countSourceUnits": True},
                "netRateScheme": {"uid": "S1", "name": "Standard"} if scheme else None,
                "analyseLanguageParts": []}
        members = {}
        for part_id, target, job_words in parts:
            body["analyseLanguageParts"].append({
                "id": part_id, "sourceLang": "en", "targetLang": target,
                "data": counts(sum(job_words.values())),
                "discountedData": counts(sum(job_words.values()), True) if scheme else None,
                "jobs": [{"uid": j, "jobUid": "SOURCE" + j, "filename": "same-file.txt"} for j in job_words][:100],
                "transMemories": [{"uid": "TM1", "name": "Existing memory"}],
            })
            members[part_id] = [{"uid": j, "filename": "same-file.txt", "data": counts(w, version=1),
                                 "discountedData": counts(w, True, 1) if scheme else None} for j, w in job_words.items()]
        return {"body": body, "members": members}

    def refs(self):
        keys = ("uid", "name", "type", "dateCreated", "outdated", "importStatus", "netRateScheme")
        return [{k: a["body"][k] for k in keys if k in a["body"]} for a in self.analyses.values()]

    def __call__(self, path, params):
        if path in self.overrides:
            value = self.overrides[path]
            return value(params) if callable(value) else response(*value)
        parts = path.split("/")
        if path == "/api2/v1/auth/whoAmI":
            return response(200, {"organization": {"uid": "ORG1"}, "user": {"role": "PROJECT_MANAGER"}, "csrfToken": "not-exported"})
        if path == "/api2/v1/projects":
            rows = [p for p in self.projects.values() if "clientId" not in params or str(params["clientId"]) == p["client"]["id"]]
            return response(200, page(rows, params))
        if len(parts) >= 5 and parts[3] == "projects":
            project_uid = parts[4]
            if len(parts) == 5:
                return response(200, self.projects[project_uid])
            if parts[-1] == "workflowSteps":
                return response(200, {"projectWorkflowSteps": [{"workflowLevel": 1}, {"workflowLevel": 2}] if project_uid == "P1" else []})
            if parts[-1] == "jobs":
                rows = [j for j in self.jobs.values() if j["project"]["uid"] == project_uid and j["workflowLevel"] == params["workflowLevel"]]
                return response(200, page(rows, params))
            if len(parts) == 7 and parts[5] == "jobs":
                return response(200, self.jobs[parts[6]])
            if parts[-1] == "analyses":
                refs = [ref for ref in self.refs() if self.analyses[ref["uid"]]["body"]["project"]["uid"] == project_uid]
                if len(parts) == 8:
                    job_uid = parts[6]
                    refs = [ref for ref in refs if any(job_uid == j["uid"] for members in self.analyses[ref["uid"]]["members"].values() for j in members)]
                    refs += self.extra_job_refs.get(job_uid, [])
                return response(200, page(refs, params))
        if len(parts) >= 5 and parts[3] == "analyses":
            analysis = self.analyses[parts[4]]
            if len(parts) == 5:
                return response(200, analysis["body"])
            if parts[-1] == "download":
                assert params == {"format": "JSON"}
                return response(200, {"projectName": "Product German", "analyseLanguageParts": [
                    {"sourceLang": "en", "targetLang": p["targetLang"], "data": {"total": p["discountedData"] or p["data"]},
                     "jobs": [{"fileName": "same-file.txt", "data": {"total": {"words": 12.5}}}]} for p in analysis["body"]["analyseLanguageParts"]]})
            if parts[-1] == "jobs":
                return response(200, page(analysis["members"][parts[6]], params))
        if path == "/api2/v2/netRateSchemes/S1":
            return response(200, {"uid": "S1", "name": "Standard", "rates": {"tm100": 50, "nt99": 20}})
        if path == "/api2/v1/netRateSchemes/S1/workflowStepNetSchemes":
            return response(200, page([{"id": "11"}], params))
        if path == "/api2/v1/netRateSchemes/S1/workflowStepNetSchemes/11":
            return response(200, {"id": "11", "rates": {"tm100": 40}, "workflowStep": {"name": "Translation"}})
        raise AssertionError("Unexpected fixture request: " + path)
