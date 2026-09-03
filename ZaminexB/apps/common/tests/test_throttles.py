"""Rate limiters must keep enforcing their limit when the cache is down.

``SimpleRateThrottle`` keeps its history in the shared cache, and with
``IGNORE_EXCEPTIONS`` a dead or hung Redis makes ``cache.get(key, [])``
return the default ``[]`` while ``cache.set`` is swallowed — so the counter
never grows and every request passes. These tests pin the fix: the resilient
classes fall back to a per-process history, and the backend-availability
tracking that drives the fallback is a no-op for backends whose ``set``
cannot report failure (LocMem and friends return ``None`` on success).

DRF's global throttling is disabled under ``manage.py test`` (see
``config/settings.py``), so the limiters are exercised directly rather than
over HTTP; the HTTP-level behaviour is covered by a manual run against
``runserver`` with ``REDIS_URL`` pointed at a closed port.
"""

import time

from django.test import TestCase, override_settings

from apps.common import cache_utils
from apps.common.throttles import (
    PasswordResetRateThrottle,
    ResilientAnonRateThrottle,
    ResilientScopedRateThrottle,
    ResilientUserRateThrottle,
    _clear_local_history,
)

REDIS_CACHES = {"default": {"BACKEND": "django_redis.cache.RedisCache"}}
LOCMEM_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


class _HealthyCache:
    """A working backend: writes land and report success."""

    def __init__(self):
        self.store = {}
        self.sets = 0

    def get(self, key, default=None, version=None):
        return self.store.get(key, default)

    def set(self, key, value, timeout=None, version=None):
        self.store[key] = value
        self.sets += 1
        return True


class _OutageCache:
    """A down backend seen through ``IGNORE_EXCEPTIONS``.

    Reads return the default (indistinguishable from a miss) and writes are
    swallowed — django-redis returns ``None`` in that case, which is the
    single bit of truth the availability tracking keys off.
    """

    def __init__(self):
        self.sets = 0

    def get(self, key, default=None, version=None):
        return default

    def set(self, key, value, timeout=None, version=None):
        self.sets += 1
        return None


class _Request:
    """Just enough of an ``HttpRequest`` for the throttle cache keys."""

    def __init__(self, ident="203.0.113.7"):
        self.META = {"REMOTE_ADDR": ident}
        self.user = None
        self.auth = None


class _AuthenticatedRequest(_Request):
    def __init__(self, ident="203.0.113.7", pk=42):
        super().__init__(ident)
        self.user = type("User", (), {"pk": pk, "is_authenticated": True})()


class _View:
    throttle_scope = "password_reset"


class _AvailabilityTrackingTestsMixin:
    """Shared setUp/tearDown: the tracking state is process-global."""

    def setUp(self):
        cache_utils.reset_backend_availability()
        _clear_local_history()

    def tearDown(self):
        cache_utils.reset_backend_availability()
        _clear_local_history()


class BackendAvailabilityTests(_AvailabilityTrackingTestsMixin, TestCase):
    def test_django_redis_reports_delivery(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            self.assertTrue(cache_utils.backend_reports_delivery())

    def test_locmem_does_not_report_delivery(self):
        """LocMem returns ``None`` from ``set()`` on *success*, so a ``None``
        means nothing there — the tracking must stay inert."""
        with override_settings(CACHES=LOCMEM_CACHES):
            cache_utils.reset_backend_availability()
            self.assertFalse(cache_utils.backend_reports_delivery())
            cache_utils.note_cache_delivered(None)
            self.assertTrue(cache_utils.cache_backend_available())

    def test_swallowed_write_marks_the_backend_down(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            cache_utils.note_cache_delivered(True)
            self.assertTrue(cache_utils.cache_backend_available())
            cache_utils.note_cache_delivered(None)
            self.assertFalse(cache_utils.cache_backend_available())

    def test_backend_is_reprobed_after_the_cooldown(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            original = cache_utils.BACKEND_REPROBE_SECONDS
            cache_utils.BACKEND_REPROBE_SECONDS = 0.05
            try:
                cache_utils.note_cache_delivered(None)
                self.assertFalse(cache_utils.cache_backend_available())
                time.sleep(0.08)
                self.assertTrue(cache_utils.cache_backend_available())
            finally:
                cache_utils.BACKEND_REPROBE_SECONDS = original

    def test_a_recovered_backend_is_used_again(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            cache_utils.note_cache_delivered(None)
            cache_utils.note_cache_delivered(True)
            self.assertTrue(cache_utils.cache_backend_available())


@override_settings(CACHES=REDIS_CACHES)
class ResilientThrottleTests(_AvailabilityTrackingTestsMixin, TestCase):
    """The limiters keep counting while the shared cache is unavailable.

    Run against a ``REDIS_CACHES`` setting so the availability tracking is
    armed — under the suite's default LocMem backend a ``None`` from ``set``
    means success, and the fallback would (correctly) never engage.
    """

    def _run(self, throttle, cache, requests, view=_View()):
        throttle.cache = cache
        return [throttle.allow_request(r, view) for r in requests]

    def test_stock_drf_throttle_is_defeated_by_an_outage(self):
        """Regression baseline: this is the bug the mixin exists to fix."""
        from rest_framework.throttling import ScopedRateThrottle

        results = self._run(
            ScopedRateThrottle(), _OutageCache(), [_Request() for _ in range(8)]
        )
        self.assertEqual(results.count(False), 0)

    def test_scoped_throttle_still_limits_during_an_outage(self):
        results = self._run(
            ResilientScopedRateThrottle(),
            _OutageCache(),
            [_Request() for _ in range(8)],
        )
        # password_reset is 5/hour: the first five pass, the rest are denied.
        self.assertEqual(results, [True] * 5 + [False] * 3)

    def test_password_reset_throttle_still_limits_during_an_outage(self):
        results = self._run(
            PasswordResetRateThrottle(),
            _OutageCache(),
            [_Request() for _ in range(8)],
        )
        self.assertEqual(results, [True] * 5 + [False] * 3)

    def test_anon_throttle_still_limits_during_an_outage(self):
        throttle = ResilientAnonRateThrottle()
        results = self._run(
            throttle, _OutageCache(), [_Request() for _ in range(65)]
        )
        # anon is 60/min.
        self.assertEqual(results.count(False), 5)

    def test_user_throttle_still_limits_during_an_outage(self):
        throttle = ResilientUserRateThrottle()
        view = type("V", (), {"throttle_scope": None})()
        results = self._run(
            throttle,
            _OutageCache(),
            [_AuthenticatedRequest() for _ in range(305)],
            view=view,
        )
        # user is 300/min.
        self.assertEqual(results.count(False), 5)

    def test_per_client_isolation_is_preserved(self):
        """The fallback must not merge different clients into one counter."""
        cache = _OutageCache()
        throttle = ResilientScopedRateThrottle()
        throttle.cache = cache
        a = _Request(ident="203.0.113.1")
        b = _Request(ident="203.0.113.2")
        for _ in range(5):
            self.assertTrue(throttle.allow_request(a, _View()))
        self.assertFalse(throttle.allow_request(a, _View()))
        self.assertTrue(throttle.allow_request(b, _View()))

    def test_doomed_round_trips_are_skipped_while_down(self):
        cache = _OutageCache()
        throttle = ResilientScopedRateThrottle()
        throttle.cache = cache
        throttle.allow_request(_Request(), _View())
        sets_after_first = cache.sets
        for _ in range(10):
            throttle.allow_request(_Request(), _View())
        # The first request probes the backend; the rest use the local store.
        self.assertEqual(cache.sets, sets_after_first)

    def test_healthy_backend_uses_the_shared_cache(self):
        cache = _HealthyCache()
        throttle = ResilientScopedRateThrottle()
        results = self._run(throttle, cache, [_Request() for _ in range(8)])
        self.assertEqual(results, [True] * 5 + [False] * 3)
        self.assertTrue(cache.store, "history must live in the shared cache")
        self.assertEqual(cache.sets, 5)

    def test_history_seeded_from_the_failed_write_counts_the_next_request(self):
        """The request that observes the outage must not leak a second one."""
        cache = _OutageCache()
        throttle = ResilientScopedRateThrottle()
        throttle.cache = cache
        results = [throttle.allow_request(_Request(), _View()) for _ in range(8)]
        # No extra allowances beyond the configured five.
        self.assertEqual(results.count(True), 5)
