"""Phase 2 — Redis infrastructure: cache_utils + CACHES configuration.

The contract under test is the helper layer's behaviour (versioned keys,
JSON/Decimal payloads, fail-open semantics, herd-safe compute) plus the
settings wiring — it is backend-agnostic, so the suite runs against the
configured default cache (LocMem without REDIS_URL). The one
backend-specific test uses the real django-redis client pointed at a closed
port to prove the roadmap's acceptance criterion end to end:
**a dead Redis → the request still succeeds.**
"""

import os
import threading
import time
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client, override_settings

from apps.common import cache_utils
from apps.common.cache_utils import (
    CACHE_VERSION,
    cache_delete,
    cache_get,
    cache_or_compute,
    cache_set,
    make_key,
)


def _never():
    raise AssertionError("compute must not run")


class MakeKeyTests(TestCase):
    def test_key_is_versioned_and_namespaced(self):
        self.assertEqual(
            make_key("report", "property", 42),
            f"zaminex:{CACHE_VERSION}:report:property:42",
        )

    def test_key_sanitises_separators_and_drops_empty_parts(self):
        self.assertEqual(
            make_key("stats", "a:b", "", "x/y", 7),
            f"zaminex:{CACHE_VERSION}:stats:a_b:x_y:7",
        )

    def test_bumping_the_version_changes_every_key(self):
        old = make_key("ai", "property", 1)
        with mock.patch.object(cache_utils, "CACHE_VERSION", "v2"):
            new = make_key("ai", "property", 1)
        self.assertNotEqual(old, new)


class JsonRoundTripTests(TestCase):
    def test_decimal_none_and_persian_text_round_trip_exactly(self):
        value = {
            "price": Decimal("12345.6700"),
            "total": Decimal("999999999999"),
            "nothing": None,
            "persian": "قیمت به ریال",
            "nested": {"a": [1, 2, 3], "b": Decimal("0.1")},
        }
        key = make_key("test", "roundtrip")
        self.assertTrue(cache_set(key, value, 60))
        out = cache_get(key)
        self.assertEqual(out, value)
        # Decimals must come back as Decimal — no float drift.
        self.assertIsInstance(out["price"], Decimal)
        self.assertEqual(out["price"], Decimal("12345.6700"))
        self.assertIsInstance(out["nested"]["b"], Decimal)
        self.assertIsNone(out["nothing"])

    def test_get_on_missing_key_is_none(self):
        self.assertIsNone(cache_get(make_key("test", "missing-key")))

    def test_corrupt_payload_is_a_miss_not_an_error(self):
        from django.core.cache import cache

        key = make_key("test", "corrupt")
        cache.set(key, "this is {not json", 60)
        try:
            self.assertIsNone(cache_get(key))
        finally:
            cache_delete(key)

    def test_foreign_non_string_payload_is_a_miss(self):
        # Another subsystem (e.g. a throttle counter) may store raw values
        # through the same backend — reads of those are misses, not crashes.
        from django.core.cache import cache

        key = make_key("test", "foreign")
        cache.set(key, 42, 60)
        try:
            self.assertIsNone(cache_get(key))
        finally:
            cache_delete(key)


class VersioningTests(TestCase):
    def test_bumping_the_version_invalidates_previous_entries(self):
        key_v1 = make_key("stats", "neighborhoods")
        self.assertTrue(cache_set(key_v1, {"avg": Decimal("1000")}, 300))
        self.assertIsNotNone(cache_get(key_v1))

        # A version bump (new code) reads a different key → guaranteed miss;
        # the stale v1 entry simply expires on its own.
        with mock.patch.object(cache_utils, "CACHE_VERSION", "v2"):
            self.assertIsNone(cache_get(make_key("stats", "neighborhoods")))

        # The old key still exists (targeted invalidation, no flush).
        self.assertIsNotNone(cache_get(key_v1))


class FailOpenTests(TestCase):
    """A dead cache backend degrades to a miss — never an exception."""

    class _DeadCache:
        def __getattr__(self, _name):
            raise ConnectionError("redis is down")

    def test_helpers_survive_a_dead_backend(self):
        key = make_key("test", "dead")
        with mock.patch.object(cache_utils, "_cache", return_value=self._DeadCache()):
            self.assertIsNone(cache_get(key))
            self.assertFalse(cache_set(key, {"x": 1}, 60))
            cache_delete(key)  # must not raise

            calls = []

            def compute():
                calls.append(1)
                return {"ok": True}

            # Unknown lock state → fail fast: compute locally, no waiting.
            self.assertEqual(cache_or_compute(key, compute, 60), {"ok": True})
            self.assertEqual(calls, [1])


class DeadRedisRequestTests(TestCase):
    """Roadmap acceptance: mock Redis down → the request succeeds.

    Uses the real django-redis backend pointed at a closed port: every cache
    operation fails fast, ``IGNORE_EXCEPTIONS`` turns the failure into a
    miss, and DRF's throttle — a real cache consumer on every request — must
    not break the request.
    """

    DEAD_CACHE = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            # Nothing listens on this port → instant connection refused.
            "LOCATION": "redis://127.0.0.1:63999/0",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,
                "SOCKET_CONNECT_TIMEOUT": 0.2,
                "SOCKET_TIMEOUT": 0.2,
            },
        }
    }

    def test_request_succeeds_with_a_dead_redis(self):
        from apps.accounts.models import User

        user = User.objects.create_user(
            username="dead-redis-user", password="pw", role="AGENT"
        )
        client = Client(SERVER_NAME="localhost")
        client.force_login(user)
        with override_settings(CACHES=self.DEAD_CACHE):
            res = client.get("/properties/api/properties/", {"page_size": 5})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["results"]), 0)


class CacheOrComputeTests(TestCase):
    def test_hit_avoids_compute(self):
        key = make_key("test", "hit")
        self.assertTrue(cache_set(key, {"v": 1}, 60))
        self.assertEqual(
            cache_or_compute(key, _never, 60),
            {"v": 1},
        )

    def test_miss_computes_stores_and_then_hits(self):
        key = make_key("test", "miss")
        calls = []

        def compute():
            calls.append(1)
            return {"n": Decimal("1.5")}

        self.assertEqual(cache_or_compute(key, compute, 60), {"n": Decimal("1.5")})
        self.assertEqual(calls, [1])
        self.assertEqual(cache_get(key), {"n": Decimal("1.5")})

        # Second call: served from the cache, no recompute.
        self.assertEqual(cache_or_compute(key, _never, 60), {"n": Decimal("1.5")})
        self.assertEqual(calls, [1])

    def test_lock_waits_for_the_other_worker(self):
        # A simulated "winner" holds the lock and publishes the value after
        # a moment; the waiter must pick it up without computing itself.
        from django.core.cache import cache

        key = make_key("test", "lock")
        cache.add(key + ":lock", "1", 10)

        def winner():
            time.sleep(0.25)
            cache_set(key, {"from": "winner"}, 60)
            cache_delete(key + ":lock")

        thread = threading.Thread(target=winner)
        thread.start()
        try:
            calls = []
            value = cache_or_compute(
                key, lambda: calls.append(1) or {"from": "local"}, 60
            )
        finally:
            thread.join()
        self.assertEqual(value, {"from": "winner"})
        self.assertEqual(calls, [])

    def test_lock_timeout_falls_back_to_local_compute(self):
        # The "holder" dies without publishing: after the (shortened) wait
        # the waiter must compute locally — correctness over the optimisation.
        from django.core.cache import cache

        key = make_key("test", "lock-timeout")
        cache.add(key + ":lock", "1", 10)
        with mock.patch.object(cache_utils, "_LOCK_MAX_WAIT", 0.35):
            calls = []
            value = cache_or_compute(
                key, lambda: calls.append(1) or {"from": "local"}, 60
            )
        self.assertEqual(value, {"from": "local"})
        self.assertEqual(calls, [1])
        self.assertEqual(cache_get(key), {"from": "local"})

    def test_lock_disabled_computes_directly(self):
        key = make_key("test", "nolock")
        calls = []
        value = cache_or_compute(
            key, lambda: calls.append(1) or {"x": 2}, 60, lock=False
        )
        self.assertEqual(value, {"x": 2})
        self.assertEqual(calls, [1])
        self.assertEqual(cache_get(key), {"x": 2})


class CacheSettingsTests(TestCase):
    def test_defaults_to_locmem_without_redis_url(self):
        from config.settings import _cache_settings

        env = dict(os.environ)
        env.pop("REDIS_URL", None)
        with mock.patch.dict(os.environ, env, clear=True):
            caches = _cache_settings()
        self.assertEqual(
            caches["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )

    def test_uses_django_redis_when_redis_url_is_set(self):
        from config.settings import _cache_settings

        with mock.patch.dict(
            os.environ, {"REDIS_URL": "redis://cache.internal:6379/0"}
        ):
            caches = _cache_settings()
        default = caches["default"]
        self.assertEqual(default["BACKEND"], "django_redis.cache.RedisCache")
        self.assertEqual(default["LOCATION"], "redis://cache.internal:6379/0")
        # Fail-open + bounded latency — the two safety properties of the
        # Redis configuration.
        self.assertTrue(default["OPTIONS"]["IGNORE_EXCEPTIONS"])
        self.assertLessEqual(default["OPTIONS"]["SOCKET_TIMEOUT"], 1.0)
        self.assertLessEqual(default["OPTIONS"]["SOCKET_CONNECT_TIMEOUT"], 1.0)

    def test_blank_redis_url_stays_on_locmem(self):
        from config.settings import _cache_settings

        with mock.patch.dict(os.environ, {"REDIS_URL": "   "}):
            caches = _cache_settings()
        self.assertEqual(
            caches["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )
