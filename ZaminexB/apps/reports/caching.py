"""Phase 4 — caches for the heavy report computations.

The expensive pure functions in ``reports/services.py`` are wrapped with
versioned, fail-open cache entries (the Phase-2 infrastructure). The
computations themselves are untouched; these wrappers are the only change at
the call sites, and every consumer of a report (JSON page, CSV export, PDF
export, the AI description input) goes through the same cache.

Keys (all under the Phase-2 versioned namespace, so a ``CACHE_VERSION`` bump
invalidates everything at once):

* ``report:property:<id>`` → ``{"<range-tag>": report, …}`` — one entry per
  date-range variant. All variants of a property live under one key so the
  signal-driven invalidation deletes exactly one key per property.
* ``report:consultant:<user_id>`` → the scope report.

TTLs (roadmap): 120 s property reports, 60 s scope reports. Invalidation is
signal-driven (``apps.common.cache_invalidation``): a save on a related row
deletes the key immediately; the TTL is the backstop.

Concurrency: reads/writes run under the per-key ``with_lock`` (property) or
``cache_or_compute`` (scope report), so a cold-start herd pays for one
computation, not N.
"""

from __future__ import annotations

import datetime
from typing import Any

from apps.common import cache_utils

from .services import compute_consultant_scope_report, compute_property_report

PROPERTY_REPORT_TTL = 120  # seconds (roadmap)
SCOPE_REPORT_TTL = 60  # seconds (roadmap)

# Lock bounds for the property-report wrapper: the computation is a handful
# of indexed queries (tens of ms), so short bounds are ample.
_REPORT_LOCK_TIMEOUT = 10
_REPORT_LOCK_WAIT = 2


def _range_tag(filters: dict | None) -> str:
    """Stable tag for a date-range filter set (the report's only variant)."""
    filters = filters or {}
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    return (
        f"{date_from.isoformat() if date_from is not None else ''}"
        f"|{date_to.isoformat() if date_to is not None else ''}"
    )


def _json_stable(value: Any) -> Any:
    """Make a computed report survive a JSON round trip with the same shape.

    The report's ``meta.filters`` carries ``datetime.date`` objects; the JSON
    codec serialises them to ISO strings and they come back as strings.
    Normalising up-front means the first (uncached) caller and every cached
    caller see identical structures — and both match the wire format, which
    was already strings. (Decimals need no help: the codec round-trips them
    exactly.)
    """
    if isinstance(value, dict):
        return {k: _json_stable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_stable(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def cached_property_report(prop, *, filters: dict | None = None) -> dict[str, Any]:
    """``compute_property_report`` behind a per-property cache.

    Different date-range variants coexist under one key (tagged entries), so
    invalidation of the property removes every variant in one delete.
    Fail-open: with the cache down every call computes fresh — a cache
    problem degrades to the uncached behaviour, never to an error.
    """
    key = cache_utils.make_key("report", "property", prop.pk)
    tag = _range_tag(filters)
    lock_key = key + ":lock"
    with cache_utils.with_lock(
        lock_key, timeout=_REPORT_LOCK_TIMEOUT, wait=_REPORT_LOCK_WAIT
    ):
        entries = cache_utils.cache_get(key)
        if not isinstance(entries, dict):
            entries = {}
        entry = entries.get(tag)
        if isinstance(entry, dict):
            return entry

        report = _json_stable(compute_property_report(prop, filters=filters))
        entries[tag] = report
        cache_utils.cache_set(key, entries, PROPERTY_REPORT_TTL)
        return report


def cached_consultant_scope_report(user) -> dict[str, Any]:
    """``compute_consultant_scope_report`` behind a per-user cache.

    ``cache_or_compute`` provides the herd lock; the key is per user because
    the scope (and therefore the data) is per user — no cross-user exposure.
    """
    key = cache_utils.make_key("report", "consultant", user.pk)

    def compute():
        return compute_consultant_scope_report(user)

    return cache_utils.cache_or_compute(key, compute, SCOPE_REPORT_TTL)
