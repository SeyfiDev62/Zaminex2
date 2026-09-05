"""A ``cached_db`` session backend that skips the cache while it is down.

``SESSION_SAVE_EVERY_REQUEST`` is on (the 12-hour idle timeout needs a
sliding expiry), so *every* authenticated request makes at least two cache
round trips through ``SESSION_ENGINE`` — a read in ``load()`` and a write in
``save()``. Those go through ``django.core.cache`` directly, not through
``apps.common.cache_utils``, so the circuit breaker there cannot see them:
against a Redis that accepts the connection and never replies, the sessions
alone were still charging two socket timeouts on every request after the
rest of the cache had been short-circuited.

This subclass wraps the session cache in a guard that consults the same
availability tracking. It is deliberately *not* a fallback store: a session
kept in one worker's memory would be invisible to every other worker, so
while the backend is marked down the guard simply declines to touch it and
``cached_db`` does what it already does on a miss — read from and write to
``django_session``. Slower, but correct, and identical to the behaviour the
fail-open configuration produces today, minus the timeout.

Async variants are guarded too: ``django.contrib.sessions.middleware`` calls
them for async views, and an unimplemented ``aget`` would raise rather than
degrade.
"""

from __future__ import annotations

from typing import Any

from django.contrib.sessions.backends.cached_db import SessionStore as CachedDBSessionStore

from apps.common import cache_utils

__all__ = ["SessionStore"]


class _SessionCacheGuard:
    """Skip cache operations while the backend is marked down.

    Only the methods ``cached_db.SessionStore`` uses are implemented; every
    other attribute is delegated, so the guard stays transparent if Django
    adds a call.
    """

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        # Reaching ``_wrapped`` through here would recurse forever before
        # ``__init__`` has assigned it (unpickling, copy, some repr helpers).
        if name == "_wrapped":
            raise AttributeError(name)
        return getattr(self._wrapped, name)

    def get(self, key: str, default: Any = None, version: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            return default
        return self._wrapped.get(key, default, version)

    def set(self, key: str, value: Any, timeout: Any = None, version: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            return None
        delivered = self._wrapped.set(key, value, timeout, version)
        cache_utils.note_cache_delivered(delivered)
        return delivered

    def delete(self, key: str, version: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            return None
        return self._wrapped.delete(key, version)

    def __contains__(self, key: str) -> bool:
        if not cache_utils.cache_backend_available():
            return False
        return key in self._wrapped

    # -- async mirror ----------------------------------------------------
    async def aget(self, key: str, default: Any = None, version: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            return default
        return await self._wrapped.aget(key, default, version)

    async def aset(self, key: str, value: Any, timeout: Any = None, version: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            return None
        delivered = await self._wrapped.aset(key, value, timeout, version)
        cache_utils.note_cache_delivered(delivered)
        return delivered

    async def adelete(self, key: str, version: Any = None) -> Any:
        if not cache_utils.cache_backend_available():
            return None
        return await self._wrapped.adelete(key, version)


class SessionStore(CachedDBSessionStore):
    """``cached_db`` with a cache guard in front of the session cache."""

    def __init__(self, session_key: str | None = None) -> None:
        super().__init__(session_key)
        if not isinstance(self._cache, _SessionCacheGuard):
            self._cache = _SessionCacheGuard(self._cache)
