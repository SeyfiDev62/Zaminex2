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

import logging
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
    """Shared setUp/tearDown: the tracking state is process-global.

    The transition warnings are silenced here so a green run stays quiet;
    :meth:`assertLogs` re-enables the level for the tests that assert on the
    messages themselves.
    """

    def setUp(self):
        cache_utils.reset_backend_availability()
        _clear_local_history()
        logging.getLogger("apps.common.cache_utils").setLevel(logging.CRITICAL)

    def tearDown(self):
        cache_utils.reset_backend_availability()
        _clear_local_history()
        logging.getLogger("apps.common.cache_utils").setLevel(logging.NOTSET)


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


class OutageLoggingTests(_AvailabilityTrackingTestsMixin, TestCase):
    """A fail-open backend must not also be a silent one.

    ``IGNORE_EXCEPTIONS`` swallowing an error is the point; swallowing it
    *without a trace* is what made a dead Redis undiagnosable — no 500, no
    warning, nothing to grep for.
    """

    DEAD_CACHE = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:63999/0",  # nothing listens here
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,
                "SOCKET_CONNECT_TIMEOUT": 0.2,
                "SOCKET_TIMEOUT": 0.2,
            },
        }
    }

    def test_django_redis_logging_is_enabled(self):
        from django.conf import settings

        self.assertTrue(settings.DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS)

    def test_the_underlying_error_is_logged_with_its_cause(self):
        """The swallowed exception itself, so "connection refused" can be
        told apart from "read timed out" or an OOM rejection."""
        from django_redis.cache import RedisCache

        backend = RedisCache(
            self.DEAD_CACHE["default"]["LOCATION"], self.DEAD_CACHE["default"]
        )
        self.assertTrue(backend._log_ignored_exceptions)
        with self.assertLogs("django_redis.cache", level="ERROR") as cm:
            self.assertIsNone(backend.get("probe:missing"))  # still fail-open
        self.assertIn("Exception ignored", cm.output[0])
        self.assertIn("refused", cm.output[0].lower())

    def test_an_outage_logs_one_warning_not_one_per_request(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            with self.assertLogs("apps.common.cache_utils", level="WARNING") as cm:
                for _ in range(20):
                    cache_utils.note_cache_delivered(None)
            self.assertEqual(len(cm.output), 1)
            self.assertIn("not delivering writes", cm.output[0])

    def test_a_still_down_reprobe_does_not_log_again(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            cache_utils.note_cache_delivered(None)
            with self.assertNoLogs("apps.common.cache_utils", level="WARNING"):
                for _ in range(5):
                    cache_utils.note_cache_delivered(None)

    def test_recovery_logs_one_info_line(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            cache_utils.note_cache_delivered(None)
            with self.assertLogs("apps.common.cache_utils", level="INFO") as cm:
                cache_utils.note_cache_delivered(True)
            self.assertEqual(len(cm.output), 1)
            self.assertIn("delivering writes again", cm.output[0])

    def test_a_healthy_backend_logs_nothing(self):
        with override_settings(CACHES=REDIS_CACHES):
            cache_utils.reset_backend_availability()
            with self.assertNoLogs("apps.common.cache_utils", level="INFO"):
                for _ in range(5):
                    cache_utils.note_cache_delivered(True)


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


class ThrottleRateCoverageTests(TestCase):
    """Every configured rate must have something behind it.

    ``login``, ``export`` and ``file_upload`` sat in ``DEFAULT_THROTTLE_RATES``
    with no view naming them, which reads like protection and is none: the
    next person to look at the settings reasonably concludes exports are
    capped at 10/hour when they are not. Walking the URLconf turns that into
    a suite failure instead of a false belief.
    """

    # Scopes carried by the default throttle classes, which apply to any view
    # that does not declare its own ``throttle_classes``. Named here rather
    # than read from ``DEFAULT_THROTTLE_CLASSES`` because the test runner
    # empties that setting (see ``config/settings.py``).
    DEFAULT_CLASS_SCOPES = {"anon", "user"}

    def _reachable_api_views(self):
        from django.urls import get_resolver
        from rest_framework.views import APIView

        found = []

        def walk(resolver):
            for pattern in resolver.url_patterns:
                if hasattr(pattern, "url_patterns"):
                    walk(pattern)
                    continue
                callback = pattern.callback
                cls = getattr(callback, "view_class", None) or getattr(callback, "cls", None)
                if cls is not None and issubclass(cls, APIView):
                    found.append(cls)

        walk(get_resolver())
        return found

    def _scopes_in_force(self):
        """Every throttle scope a reachable view can actually apply."""
        from rest_framework.throttling import ScopedRateThrottle

        used = set(self.DEFAULT_CLASS_SCOPES)
        for view in self._reachable_api_views():
            classes = getattr(view, "throttle_classes", ()) or ()
            for cls in classes:
                # A throttle class may carry its own scope (e.g.
                # PasswordResetRateThrottle).
                scope = getattr(cls, "scope", None)
                if scope:
                    used.add(scope)
            # A ScopedRateThrottle subclass has scope = None on the class and
            # resolves it from the view at request time, so the view's
            # `throttle_scope` attribute is the real source.
            if any(issubclass(c, ScopedRateThrottle) for c in classes):
                view_scope = getattr(view, "throttle_scope", None)
                if view_scope:
                    used.add(view_scope)
        return used

    def test_the_urlconf_walk_finds_views(self):
        """Guards the guard: an empty walk would make every other assertion
        here vacuously true."""
        self.assertGreater(len(self._reachable_api_views()), 50)

    def test_the_walk_sees_the_scoped_views(self):
        """Guards the guard again: a scoped throttle carries its scope on the
        *view*, not on the class, so a walk that only read class attributes
        would silently miss both of these."""
        self.assertIn("ai", self._scopes_in_force())
        self.assertIn("geocode", self._scopes_in_force())

    def test_no_configured_rate_is_dead(self):
        from django.conf import settings

        configured = set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        self.assertEqual(
            configured - self._scopes_in_force(),
            set(),
            "rates declared with no view behind them protect nothing",
        )

    def test_no_view_uses_a_scope_without_a_rate(self):
        """The mirror image: a view naming an unconfigured scope makes DRF
        raise ImproperlyConfigured on the first request."""
        from django.conf import settings

        configured = set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        declared = self._scopes_in_force() - self.DEFAULT_CLASS_SCOPES
        self.assertEqual(
            declared - configured,
            set(),
            "views whose throttle_scope has no matching rate",
        )

    def test_the_expected_scopes_are_the_ones_in_force(self):
        """Pins the actual coverage, so adding or dropping a scope is a
        deliberate, reviewed change."""
        self.assertEqual(
            self._scopes_in_force(),
            {"anon", "user", "password_reset", "ai", "geocode"},
        )
