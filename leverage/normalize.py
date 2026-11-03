"""Lossless category discovery and explicit separation of returned and derived values."""

import hashlib
import json
import re

from .numeric import difference, number, percentage


def record_id(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def basis(settings):
    flag = (settings or {}).get("countSourceUnits")
    return "source" if flag is True else "target" if flag is False else "unknown"


def scheme_reference(value):
    if not isinstance(value, dict):
        return False
    scheme_uid, scheme_id = value.get("uid"), value.get("id")
    return bool((isinstance(scheme_uid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", scheme_uid)) or
                (type(scheme_id) in {str, int} and str(scheme_id).isdecimal() and int(scheme_id) > 0))


def totals(raw, net, has_scheme):
    raw, net = raw or {}, net or {}
    gross = (raw.get("all") or {}) if raw.get("available") is not False else {}
    discounted = (net.get("all") or {}) if has_scheme and net.get("available") is True else {}
    words, net_words = number(gross.get("words")), number(discounted.get("words"))
    savings = difference(words, net_words) if (raw.get("available") is True and
              raw.get("estimate") is not True and net.get("estimate") is not True) else None
    return {
        "gross_words": words, "discounted_words": net_words, "word_savings": savings,
        "word_savings_percent": percentage(savings, words),
        "raw_characters": number(gross.get("characters")), "raw_segments": number(gross.get("segments")),
        "discounted_characters": number(discounted.get("characters")),
        "discounted_segments": number(discounted.get("segments")),
        "raw_available": raw.get("available"), "discounted_available": net.get("available"),
        "raw_estimate": raw.get("estimate"), "discounted_estimate": net.get("estimate"),
    }


def _leaves(value, prefix=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in {"available", "estimate"}:
                yield from _leaves(item, prefix + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, prefix + (str(index),))
    elif prefix:
        # Retain null leaves too; absence is not a manufactured zero.
        yield prefix, number(value)


def measures(context, raw, net, has_scheme):
    """Keep original field paths, including new/unknown categories and units."""
    raw, net = raw or {}, net or {}
    # Metadata lives in dimension tables, rather than being duplicated (including
    # whole settings/TM/provider objects) for every numeric leaf in a large run.
    dimensions = {key: context.get(key) for key in (
        "project_uid", "project_name", "client_id", "client_name", "analysis_uid", "analysis_name",
        "analysis_type", "analysis_created_at", "settings_fingerprint", "count_basis", "language_part_id",
        "source_language", "target_language", "grain", "selected", "endpoint_version", "job_uid",
        "filename", "workflow_level", "language_raw_estimate", "language_raw_available",
    )}
    scheme = context.get("net_rate_scheme")
    dimensions["net_rate_scheme_uid"] = scheme.get("uid") if isinstance(scheme, dict) else None
    raw_leaves, net_leaves = dict(_leaves(raw)), dict(_leaves(net))
    for path in sorted(raw_leaves.keys() | net_leaves.keys()):
        raw_value, net_value = raw_leaves.get(path), net_leaves.get(path)
        raw_valid = (raw.get("available") is True and raw.get("estimate") is not True and
                     context.get("language_raw_estimate") is not True)
        net_valid = (has_scheme and net.get("available") is True and net.get("estimate") is not True and
                     context.get("language_discounted_estimate") is not True)
        yield {
            **dimensions, "record_id": record_id(context["record_id"], path),
            "category": path[0], "band": ".".join(path[1:-1]) or None,
            "unit": path[-1], "field_path": ".".join(path),
            "raw_value": raw_value, "discounted_value": net_value,
            "raw_available": raw.get("available"), "discounted_available": net.get("available"),
            "raw_estimate": raw.get("estimate"), "discounted_estimate": net.get("estimate"),
            "word_savings": difference(raw_value, net_value)
            if path[-1] == "words" and raw_valid and net_valid else None,
        }
