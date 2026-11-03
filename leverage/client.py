"""GET-only TMS client with bounded retries and verified complete pagination."""

from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import hashlib
import json
import random
import re
import time

import requests

from .numeric import dumps


class ApiError(RuntimeError):
    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


def reject_constant(value):
    # Python's JSON decoder otherwise accepts NaN/Infinity, which are not JSON
    # numbers and would make sanitized snapshots/export fail much later.
    raise ValueError("Nonfinite JSON constant")


class TmsClient:
    def __init__(self, settings, session=None, sleep=time.sleep, clock=time.monotonic):
        self.settings = settings
        self.session = session or requests.Session()
        self._sleep, self._clock, self._last_request = sleep, clock, None
        self.snapshots = {}
        self.request_count = 0
        self.failed_request_count = 0

    def close(self):
        self.session.close()

    def safe(self, value):
        """Sanitize archived payloads too, including unexpected echoes of credentials."""
        if isinstance(value, dict):
            return {k: ("[REDACTED]" if re.search(r"token|password|secret|authorization|referer", k, re.I)
                        else self.safe(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in (self.settings.authorization, self.settings.authorization.split(" ", 1)[1]):
                value = value.replace(secret, "[REDACTED]")
        return value

    def snapshot(self, kind, key, payload, endpoint_version):
        clean = self.safe(payload)
        encoded = dumps(clean).encode("utf-8")
        self.snapshots[(kind, str(key))] = {
            "kind": kind, "key": self.safe(str(key)), "endpoint_version": endpoint_version,
            "sha256_sanitized_payload": hashlib.sha256(encoded).hexdigest(), "payload": clean,
        }

    def _pace(self):
        if self._last_request is not None:
            self._sleep(max(0, 1 / self.settings.requests_per_second - (self._clock() - self._last_request)))
        self._last_request = self._clock()

    @staticmethod
    def _delay(response, attempt):
        if response is not None:
            value = response.headers.get("Retry-After")
            try:
                delay = float(value)
                if delay >= 0 and delay < float("inf"):
                    return delay
            except (ValueError, TypeError):
                try:
                    return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
            if response.status_code == 429 and "Ratelimit-Limit" in response.headers:
                return 60 + random.uniform(0, 1)
        return min(30, 2 ** attempt) + random.uniform(0, 0.25)

    def get(self, path, params=None):
        # Reject absolute/relative escapes, query injection and every content mutation.
        if not re.fullmatch(r"/api2/v[1-4]/[A-Za-z0-9_/%.-]+", path) or ".." in path or "%" in path:
            raise ApiError("UNSAFE_PATH", "Only versioned TMS resource paths are permitted.")
        for attempt in range(self.settings.retries + 1):
            self._pace()
            response = None
            try:
                self.request_count += 1
                response = self.session.get(
                    self.settings.base_url + path, params=params or {},
                    headers={"Authorization": self.settings.authorization, "Accept": "application/json",
                             "User-Agent": "Phrase-Usage-Leverage-Extractor/1.0"},
                    timeout=(10, 60), allow_redirects=False,
                )
            except requests.RequestException:
                self.failed_request_count += 1
                if attempt == self.settings.retries:
                    raise ApiError("TRANSPORT", "TMS connection failed after retries; check connectivity and region.") from None
            else:
                status = response.status_code
                if status == 200:
                    try:
                        return response.json(parse_float=Decimal, parse_constant=reject_constant)
                    except ValueError:
                        self.failed_request_count += 1
                        raise ApiError("INVALID_JSON", "TMS returned invalid JSON; the response was not exported as counts.") from None
                    finally:
                        response.close()
                self.failed_request_count += 1
                if status not in {429, 500, 502, 503, 504}:
                    messages = {
                        401: "REST credential is missing or expired; refresh the existing TMS token securely.",
                        403: "Access denied; check project/analysis or download/net-rate permissions.",
                        404: "Resource is absent, archived, or inaccessible; nothing was restored or created.",
                        202: "The existing analysis is not ready; retry retrieval later without recalculating it.",
                    }
                    response.close()
                    raise ApiError("HTTP_" + str(status), messages.get(status, "TMS rejected the read-only request; check configuration and access."), status)
                if attempt == self.settings.retries:
                    response.close()
                    raise ApiError("HTTP_" + str(status), "TMS throttling/service failure exhausted the retry budget; retry retrieval later.", status)
            try:
                delay = self._delay(response, attempt)
            finally:
                if response is not None:
                    response.close()
            if delay > 300:
                raise ApiError("RETRY_LATER", "TMS requested a wait longer than five minutes; repeat retrieval later.")
            self._sleep(delay)

    def pages(self, path, params=None):
        """Yield partial results, then signal any truncation rather than hiding it."""
        expected = None
        seen = 0
        identities = set()
        for page in range(self.settings.max_pages):
            body = self.get(path, {**(params or {}), "pageNumber": page, "pageSize": self.settings.page_size})
            if not isinstance(body, dict) or not isinstance(body.get("content"), list):
                raise ApiError("PAGE_SCHEMA", "A paginated TMS response is missing its content array.")
            total, pages = body.get("totalElements"), body.get("totalPages")
            if (type(total) is not int or type(pages) is not int or total < 0 or pages < 0 or
                    body.get("pageNumber", page) != page):
                raise ApiError("PAGE_SCHEMA", "Invalid pagination metadata; completeness cannot be established.")
            if expected is None:
                expected = (total, pages)
            if expected != (total, pages):
                raise ApiError("PAGE_CHANGED", "TMS page counts changed during discovery; repeat retrieval for a consistent inventory.")
            content = body["content"]
            if any(not isinstance(item, dict) for item in content):
                raise ApiError("PAGE_SCHEMA", "A TMS page contains an invalid record.")
            self.snapshot("page", json.dumps([path, params or {}, page], sort_keys=True), body, path.split("/")[2])
            for item in content:
                identity = item.get("uid", item.get("id"))
                if identity is not None:
                    identity = str(identity)
                    if identity in identities:
                        raise ApiError("PAGE_DUPLICATE", "TMS repeated a record in one paginated collection; repeat retrieval to verify coverage.")
                    identities.add(identity)
            seen += len(content)
            yield from content
            if page + 1 >= pages:
                if seen != total:
                    raise ApiError("PAGE_TRUNCATED", "Fetched record count differs from totalElements; the run is incomplete.")
                return
            if not content:
                raise ApiError("PAGE_TRUNCATED", "TMS returned an empty intermediate page; the run is incomplete.")
        raise ApiError("PAGE_LIMIT", "Pagination safety limit reached; increase max-pages or narrow scope.")
