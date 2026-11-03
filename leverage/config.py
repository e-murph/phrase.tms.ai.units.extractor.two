"""Explicit scope and credentials; dashboard JWT URLs are never used as REST tokens."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def timestamp(value):
    """Parse an ISO date (UTC midnight) or a timezone-aware ISO timestamp."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if len(value) == 10:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, AttributeError):
            raise ConfigurationError("Use an ISO date or timestamp with a timezone.") from None
    if parsed.tzinfo is None:
        raise ConfigurationError("Timestamps must include a timezone (for example Z).")
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


@dataclass(frozen=True)
class Settings:
    authorization: str = field(repr=False)
    base_url: str = "https://cloud.memsource.com/web"
    start: datetime | None = None
    end: datetime | None = None
    project_uids: tuple = ()
    project_names: tuple = ()
    client_ids: tuple = ()
    client_names: tuple = ()
    include_archived: bool = True
    only_owner_org: bool = False
    download_json: bool = True
    summary_types: tuple = ("PreAnalyse",)
    count_basis: str = "source"
    requests_per_second: float = 2.0
    retries: int = 3
    page_size: int = 50
    max_pages: int = 20000
    report_workflow_levels: tuple = (1,)
    category_tolerance: Decimal = Decimal("0.000001")

    def __post_init__(self):
        u = urlsplit(self.base_url)
        if (u.scheme != "https" or u.hostname not in
                {"cloud.memsource.com", "us.cloud.memsource.com"} or
                u.netloc != u.hostname or u.path.rstrip("/") != "/web" or
                u.query or u.fragment):
            raise ConfigurationError("Use the official EU or US TMS HTTPS base URL ending in /web.")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        auth = self.authorization.strip()
        if "://" in auth or "\n" in auth or "\r" in auth:
            raise ConfigurationError("Dashboard URLs cannot authenticate the TMS REST API.")
        pieces = auth.split()
        if len(pieces) != 2 or pieces[0] not in {"Bearer", "ApiToken"}:
            raise ConfigurationError("Configure an existing REST token with Bearer or ApiToken authentication.")
        object.__setattr__(self, "authorization", " ".join(pieces))
        for name in ("start", "end"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, timestamp(getattr(self, name)))
        if self.start and self.end and self.start >= self.end:
            raise ConfigurationError("The start must be earlier than the exclusive end.")
        if (not math.isfinite(self.requests_per_second) or self.requests_per_second <= 0 or
                not 0 <= self.retries <= 10 or not 1 <= self.page_size <= 50 or self.max_pages < 1):
            raise ConfigurationError("Invalid pacing, retry budget, or pagination limits.")
        if self.count_basis not in {"source", "target"} or not self.summary_types:
            raise ConfigurationError("Choose source/target counting and at least one summary analysis type.")
        if not self.report_workflow_levels or any(type(level) is not int or not 1 <= level <= 15 for level in self.report_workflow_levels):
            raise ConfigurationError("Report workflow levels must be integers from 1 to 15.")
        object.__setattr__(self, "report_workflow_levels", tuple(sorted(set(self.report_workflow_levels))))
        try:
            tolerance = Decimal(str(self.category_tolerance))
            if not tolerance.is_finite() or tolerance < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise ConfigurationError("Category validation tolerance must be a finite nonnegative number.") from None
        object.__setattr__(self, "category_tolerance", tolerance)

    def in_period(self, value):
        parsed = timestamp(value)
        return (self.start is None or parsed >= self.start) and (self.end is None or parsed < self.end)

    def public(self):
        return {
            "base_url": self.base_url, "period_start": iso(self.start), "period_end": iso(self.end),
            "period_basis": "analysis_created_at", "project_uids": self.project_uids,
            "project_names": self.project_names, "client_ids": self.client_ids,
            "client_names": self.client_names, "include_archived": self.include_archived,
            "only_owner_org": self.only_owner_org, "download_json": self.download_json,
            "summary_types": self.summary_types, "count_basis": self.count_basis,
            "report_workflow_levels": self.report_workflow_levels,
            "category_tolerance": str(self.category_tolerance),
            "selection_policy": "latest_in_period_per_job_and_type; no fallback from unavailable latest",
        }
