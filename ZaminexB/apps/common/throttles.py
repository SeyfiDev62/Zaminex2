"""Rate limiters that keep enforcing their limit when the cache is down.

DRF's :class:`~rest_framework.throttling.SimpleRateThrottle` keeps its
request history in the shared cache — ``allow_request`` reads it with
``cache.get(key, [])`` and appends with ``cache.set``. With the Phase-2
``CACHES`` configuration (``IGNORE_EXCEPTIONS=True``) a dead or hung Redis
makes that read return the default ``[]`` and swallows the write, so the
counter never grows and **every** request passes: the rate limit silently
disappears, and it disappears exactly when an attacker is most likely to be
probing. Verified against a live deployment path — 8 requests to an endpoint
scoped at ``5/hour`` returned ``[200 × 5, 429 × 3]`` with Redis up and
``[200 × 8]`` with Redis down.

The classes below fall back to a per-process history in that case. A
per-worker counter degrades the limit to ``rate × workers`` instead of
unbounded, which is the deliberate trade: throttling is a cost and
abuse-control mechanism, so being slightly generous during a cache outage
beats being absent. (Login brute-force is *not* affected by any of this —
``apps/accounts/login_security.py`` keeps its counters in the database.)
"""

from __future__ import annotations

import threading
from typing import Any

from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)

from . import cache_utils

__all__ = [
    "PasswordResetRateThrottle",
    "ResilientAnonRateThrottle",
    "ResilientScopedRateThrottle",
    "ResilientUserRateThrottle",
]

# Per-process history used only while the shared cache is believed to be
# down, keyed exactly like the shared cache.
_local_history: dict[str, list[float]] = {}
_local_history_lock = threading.Lock()

# Safety net for a long outage: histories are pruned by DRF on every read,
# but the dict itself is capped so a flood of distinct keys cannot grow it
# without bound. Oldest-inserted keys are evicted first.
_LOCAL_MAX_KEYS = 10_000


def _remember_local(key: str, history: list[float]) -> None:
    with _local_history_lock:
        _local_history[key] = list(history)
        while len(_local_history) > _LOCAL_MAX_KEYS:
            _local_history.pop(next(iter(_local_history)))


def _read_local(key: str) -> list[float] | None:
    with _local_history_lock:
        history = _local_history.get(key)
        return list(history) if history is not None else None


def _clear_local_history() -> None:
    """Drop the per-process histories (tests)."""
    with _local_history_lock:
        _local_history.clear()


class _ResilientCacheProxy:
    """Cache proxy that falls back to this process while the backend is down.

    Only the two methods ``SimpleRateThrottle`` uses are implemented. Reads
    and writes go to the shared cache as long as it is believed to be
    healthy; once a write reports a swallowed error the proxy serves this
    process's own history for ``cache_backend_available``'s cooldown, which
    also stops every request from paying a socket timeout on a doomed write.
    """

    def __init__(self, shared: Any) -> None:
        self._shared = shared

    def get(self, key: str, default: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            history = _read_local(key)
            return default if history is None else history
        return self._shared.get(key, default)

    def set(self, key: str, value: Any, timeout: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            _remember_local(key, value)
            return True
        delivered = self._shared.set(key, value, timeout)
        cache_utils.note_cache_delivered(delivered)
        if not delivered and cache_utils.backend_reports_delivery():
            # The shared write was swallowed — the backend just went down.
            # Keep the history here so the *next* request is counted rather
            # than leaking through an empty counter.
            _remember_local(key, value)
        return delivered


class _ResilientRateThrottle:
    """Mixin routing throttle history through :class:`_ResilientCacheProxy`.

    Uses cooperative ``super()`` so it composes with any
    ``SimpleRateThrottle`` subclass — including ``ScopedRateThrottle``, whose
    own ``allow_request`` resolves the view's ``throttle_scope`` before
    delegating.
    """

    def allow_request(self, request: Any, view: Any) -> bool:
        # Throttle instances are built per request by DRF, so assigning to
        # the instance never re-wraps an already-wrapped proxy.
        if not isinstance(self.cache, _ResilientCacheProxy):
            self.cache = _ResilientCacheProxy(self.cache)
        return super().allow_request(request, view)


class ResilientAnonRateThrottle(_ResilientRateThrottle, AnonRateThrottle):
    """``AnonRateThrottle`` that survives a cache outage."""


class ResilientUserRateThrottle(_ResilientRateThrottle, UserRateThrottle):
    """``UserRateThrottle`` that survives a cache outage."""


class ResilientScopedRateThrottle(_ResilientRateThrottle, ScopedRateThrottle):
    """``ScopedRateThrottle`` that survives a cache outage."""


class PasswordResetRateThrottle(ResilientAnonRateThrottle):
    scope = "password_reset"
