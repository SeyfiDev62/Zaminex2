"""Versioned, fail-open cache helpers (Phase 2).

Roadmap principles implemented here:

* **Fail-open** — every function in this module swallows cache errors. A
  dead or slow backend degrades to a cache miss, never to a 500 (the
  ``CACHES`` configuration's ``IGNORE_EXCEPTIONS`` is the second line of
  defence; this module guards the decode/encode paths too).
* **Versioned keys** — every key is ``zaminex:<CACHE_VERSION>:<domain>:...``.
  When a cached payload's shape changes, bump ``CACHE_VERSION``: readers then
  miss on the old-version keys (which expire naturally) instead of
  deserialising stale data — a targeted invalidation without flushing the
  whole cache.
* **JSON payloads** — values are stored as UTF-8 JSON text (inspectable in
  ``redis-cli``), with a Decimal-safe codec so money values round-trip
  exactly (no float drift) and ``None`` survives as ``null``.
* **Thundering-herd protection** — :func:`cache_or_compute` serialises
  recomputation of a hot key with a per-key ``SET NX EX`` lock: one caller
  computes while the others briefly wait and re-read the cache.

Phase 2 is infrastructure only — no consumer is wired to this module yet
("zero risk" by construction). Phases 3–5 build on it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_VERSION",
    "make_key",
    "cache_get",
    "cache_set",
    "cache_delete",
    "cache_or_compute",
    "with_lock",
    "backend_reports_delivery",
    "note_cache_delivered",
    "cache_backend_available",
]

# Bump when the shape of a cached payload changes: readers then miss on the
# old-version keys instead of deserialising stale data. One constant covers
# every domain, so a version bump is a deliberate, global, one-line act.
CACHE_VERSION = "v1"

# cache_or_compute lock defaults (seconds).
_LOCK_SUFFIX = ":lock"
_LOCK_TIMEOUT = 10      # how long the computing worker may hold the lock
_LOCK_POLL_SECONDS = 0.1  # how often waiters re-read the cache
_LOCK_MAX_WAIT = 5.0    # give up waiting and compute locally after this


class _DecimalJSONEncoder(json.JSONEncoder):
    """JSON encoder that preserves :class:`~decimal.Decimal` exactly.

    Decimals are stored as tagged strings (``{"__zaminex_decimal__": "…"}``)
    so ``json`` never rounds them through float — money values survive the
    round trip bit-for-bit. Any other non-JSON-native type (datetime, UUID,
    …) falls back to its ``str()`` form rather than failing the store.

    Note: the fallback lives in this class's ``default`` — passing a
    ``default=`` keyword to ``json.dumps`` would override it and silently
    turn Decimals into plain strings.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return {"__zaminex_decimal__": str(o)}
        return str(o)


def _decimal_object_hook(values: dict) -> Any:
    if list(values.keys()) == ["__zaminex_decimal__"]:
        return Decimal(values["__zaminex_decimal__"])
    return values


def _encode(value: Any) -> str:
    return json.dumps(value, cls=_DecimalJSONEncoder, ensure_ascii=False)


def _decode(raw: Any) -> Any:
    """Decode a payload written by :func:`cache_set`; anything else is a miss.

    A non-string payload (e.g. a value another subsystem stored through the
    same backend) or a corrupted JSON document is treated as a miss, never
    propagated — a cache problem must not become a request problem.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw, object_hook=_decimal_object_hook)
    except (ValueError, TypeError):
        return None


def _cache():
    from django.core.cache import cache

    return cache


# ---------------------------------------------------------------------------
# Backend availability tracking
# ---------------------------------------------------------------------------
# ``IGNORE_EXCEPTIONS`` makes django-redis swallow connection errors, so a
# dead or hung backend is *invisible to a read*: ``cache.get(key, default)``
# returns the default whether the key is simply missing or the backend is
# gone. Callers that must tell "empty" apart from "unavailable" (throttling
# is the important one) therefore need a second signal.
#
# A *write* is not invisible. django-redis returns ``True`` when the value
# landed and ``None`` when ``omit_exception`` swallowed the error, so the
# return value of ``cache.set`` is the one bit of truth we get. It is
# recorded here, with a cooldown: while the backend is marked down, callers
# should serve from their own degraded path instead of paying a socket
# timeout on every operation, and after the cooldown expires the next real
# write probes it again (a half-open circuit breaker).
#
# Django's own backends (LocMem/Dummy/FileBased) return ``None`` from
# ``set()`` on *success*, so the signal only means something for the
# django-redis backend — which is also the only one that can fail silently,
# the others being in-process or on the local filesystem.
# ---------------------------------------------------------------------------

# How long a backend stays marked down before the next write probes it again:
# long enough to avoid paying the timeout on every request during an outage,
# short enough to notice recovery promptly.
BACKEND_REPROBE_SECONDS = 5.0

# Which backend the recorded state refers to. A failure observed against one
# backend says nothing about another, and ``override_settings(CACHES=…)``
# swaps backends under a running process (the test suite does this), so the
# state is dropped whenever the configured backend changes.
_backend_key: str | None = None
_backend_reports_delivery = False
_backend_marked_down = False
_backend_down_until = 0.0
_backend_state_lock = threading.Lock()


def _sync_backend() -> None:
    """Re-read the configured backend and drop stale state if it changed."""
    global _backend_key, _backend_reports_delivery, _backend_marked_down
    global _backend_down_until
    from django.conf import settings

    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if backend == _backend_key:
        return
    with _backend_state_lock:
        _backend_key = backend
        _backend_reports_delivery = "django_redis" in backend
        _backend_marked_down = False
        _backend_down_until = 0.0


def backend_reports_delivery() -> bool:
    """Whether the configured backend signals a swallowed error as ``None``.

    Only the django-redis backend does; the rest of Django's return ``None``
    from ``set()`` on *success* and cannot fail silently, so the tracking
    below is inert for them.
    """
    _sync_backend()
    return _backend_reports_delivery


def note_cache_delivered(delivered: Any) -> None:
    """Record the outcome of a cache write.

    ``delivered`` is the raw return value of ``cache.set``: truthy means the
    value landed, falsy means the backend swallowed an error. A no-op for
    backends whose ``set`` cannot report failure.

    State transitions are logged, once each, so an outage is a single
    greppable line rather than something inferred from a stream of
    ``django_redis`` tracebacks (which ``DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS``
    emits for the underlying error).
    """
    if not backend_reports_delivery():
        return
    global _backend_marked_down, _backend_down_until
    now = time.monotonic()
    with _backend_state_lock:
        was_marked_down = _backend_marked_down
        if delivered:
            _backend_marked_down = False
            _backend_down_until = 0.0
        else:
            # ``marked_down`` stays set across the cooldown so the warning
            # fires once per outage rather than once per failed write.
            _backend_marked_down = True
            _backend_down_until = now + BACKEND_REPROBE_SECONDS
    if delivered:
        if was_marked_down:
            logger.info("Cache backend is delivering writes again.")
    elif not was_marked_down:
        logger.warning(
            "Cache backend is not delivering writes — serving degraded for "
            "%.1fs before re-probing. Underlying error is logged by "
            "django_redis.cache.",
            BACKEND_REPROBE_SECONDS,
        )


def cache_backend_available() -> bool:
    """Whether the shared cache is believed to be working right now.

    ``True`` is "no failure recorded, or the re-probe cooldown has elapsed" —
    not a guarantee: the next real operation is what actually probes the
    backend, and a backend that is still down is re-marked immediately.
    """
    _sync_backend()
    if not _backend_marked_down:
        return True
    return time.monotonic() >= _backend_down_until


def reset_backend_availability() -> None:
    """Clear the recorded backend state (tests and health probes)."""
    global _backend_key, _backend_reports_delivery, _backend_marked_down
    global _backend_down_until
    with _backend_state_lock:
        _backend_key = None
        _backend_reports_delivery = False
        _backend_marked_down = False
        _backend_down_until = 0.0


def make_key(domain: str, *parts: Any) -> str:
    """Build a versioned key: ``zaminex:v1:<domain>:<part1>:<part2>:...``

    The colon is the key separator, so parts are sanitised (``:``/``/`` →
    ``_``) and empty parts are dropped. The domain names what is cached
    (``ai``, ``report``, ``stats``, …) — it is what later invalidation
    targets.
    """
    cleaned: list[str] = []
    for part in parts:
        text = str(part).strip().replace(":", "_").replace("/", "_")
        if text:
            cleaned.append(text)
    return ":".join([f"zaminex:{CACHE_VERSION}:{domain}"] + cleaned)


def cache_get(key: str) -> Any:
    """Fail-open read: any backend error (or corrupt payload) is a miss.

    While the backend is marked down this returns a miss without touching
    it, so a hung Redis costs nothing instead of a socket timeout per call.
    """
    if not cache_backend_available():
        return None
    try:
        return _decode(_cache().get(key))
    except Exception:
        return None


def cache_set(key: str, value: Any, timeout: int | float | None) -> bool:
    """Fail-open write.

    Returns ``False`` when the backend raised or is currently marked down,
    and ``True`` when no error was observed. It is not a delivery guarantee:
    the *first* write of an outage is swallowed by ``IGNORE_EXCEPTIONS``
    rather than raised, so it reports ``True`` and it is that call which
    opens the breaker for the ones after it. (Backends also differ in what
    ``set`` returns on success — LocMem ``None``, django-redis ``True`` — so
    the raw value cannot be used as the contract either way.)

    The outcome is also what feeds the availability tracker: django-redis
    returns ``None`` when ``IGNORE_EXCEPTIONS`` swallowed an error, and that
    is the only signal a silent failure leaves behind.
    """
    if not cache_backend_available():
        return False
    try:
        delivered = _cache().set(key, _encode(value), timeout)
    except Exception:
        note_cache_delivered(None)
        return False
    note_cache_delivered(delivered)
    return True


def cache_delete(key: str) -> None:
    """Fail-open delete (skipped while the backend is marked down)."""
    if not cache_backend_available():
        return
    try:
        _cache().delete(key)
    except Exception:
        pass


def cache_or_compute(
    key: str,
    compute: Callable[[], Any],
    timeout: int | float | None,
    *,
    lock: bool = True,
) -> Any:
    """Return the cached value, or compute + store it — herd-safe.

    1. Read the cache (a miss — including a dead backend — just continues).
    2. Try to take the per-key lock (``SET NX EX`` via ``cache.add``):

       * **won** → compute, store, release;
       * **lost** → another worker is computing: poll the cache briefly and
         use the value as soon as it appears (the common case);
       * **unknown** (``add`` returned nothing — the backend swallowed an
         error) → fail fast: compute locally without waiting.

    3. If the value never appeared while waiting (the lock holder may have
    died), compute locally — correctness over the optimisation.

    Notes: a computed ``None`` is stored as ``"null"`` and reads back as a
    miss, so ``None`` is never effectively cached — callers that must cache
    an "empty" result should wrap it (e.g. ``{"value": None}``). A raising
    ``compute`` propagates its exception (it is a business error, not a
    cache error); the lock expires by itself after ``_LOCK_TIMEOUT``.
    """
    value = cache_get(key)
    if value is not None:
        return value

    if not lock:
        value = compute()
        cache_set(key, value, timeout)
        return value

    lock_key = key + _LOCK_SUFFIX
    won = _try_acquire(lock_key, _LOCK_TIMEOUT)

    if won is False:
        deadline = time.monotonic() + _LOCK_MAX_WAIT
        while True:
            time.sleep(_LOCK_POLL_SECONDS)
            value = cache_get(key)
            if value is not None:
                return value
            if time.monotonic() >= deadline:
                break  # the holder died → compute locally

    value = compute()
    cache_set(key, value, timeout)
    if won:
        cache_delete(lock_key)
    return value


def _try_acquire(key: str, timeout: int | float) -> bool | None:
    """``SET NX EX`` via ``cache.add``, fail-open.

    ``True`` = we hold the lock, ``False`` = someone else does, ``None`` =
    unknown (the backend is down or swallowed an error) → the caller
    proceeds uncoordinated, which is always safe: the lock is a dedup
    optimisation, never a correctness gate.
    """
    if not cache_backend_available():
        return None
    try:
        acquired = _cache().add(key, "1", timeout)
    except Exception:
        acquired = None
    # ``add`` returning ``False`` means the backend *answered* (the key was
    # already there); only ``None`` means the failure was swallowed.
    note_cache_delivered(acquired is not None)
    return acquired


def _lock_gone(key: str) -> bool:
    if not cache_backend_available():
        return True
    try:
        return _cache().get(key) is None
    except Exception:
        return True


@contextmanager
def with_lock(
    key: str,
    timeout: int | float = _LOCK_TIMEOUT,
    wait: int | float = _LOCK_MAX_WAIT,
    poll: int | float = _LOCK_POLL_SECONDS,
) -> Iterator[bool]:
    """Best-effort exclusive lock (``SET NX EX``) as a context manager.

    Yields ``True`` when we own the lock, ``False`` when it was held by
    someone else (we waited up to ``wait`` seconds for them to finish).

    **Callers must do the work regardless of the yielded value** — the lock
    is a dedup optimisation, not a gate. The correct pattern is: enter the
    lock, *re-check* whether the work is already done (a waiter may find the
    holder's result), and do it if not. Storing the result *inside* the lock
    means a waiter that observes the lock released can rely on a re-check.

    Fail-open: if the backend is unavailable the lock is skipped and we
    proceed unprotected (a cache problem must never block the work — and
    with the cache down no coordination is possible anyway). The lock is
    released on exit **only if we actually acquired it** — we never delete a
    lock we don't own, which could delete another worker's lock. The ``EX``
    timeout is the safety net for a holder that crashes.

    ``timeout`` must outlive the critical section (e.g. an LLM call);
    ``wait`` bounds how long a waiter blocks before proceeding in parallel
    (graceful degradation: an extra computation, never a stalled request).
    """
    acquired = _try_acquire(key, timeout)
    owned = acquired is True
    if acquired is False:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not _lock_gone(key):
            time.sleep(poll)
    try:
        yield owned
    finally:
        if owned:
            cache_delete(key)
