"""Phase 2 — Redis infrastructure: cache_utils + CACHES configuration.

The contract under test is the helper layer's behaviour (versioned keys,
JSON/Decimal payloads, fail-open semantics, herd-safe compute) plus the
settings wiring — it is backend-agnostic, so the suite runs against the
configured default cache (LocMem without REDIS_URL). The one
backend-specific test uses the real django-redis client pointed at a closed
port to prove the roadmap's acceptance criterion end to end:
**a dead Redis → the request still succeeds.**
"""

import logging
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


class _CountingCache:
    """A cache double that counts the operations that actually reach it."""

    def __init__(self, healthy=True):
        self.healthy = healthy
        self.store = {}
        self.ops = 0

    def get(self, key, default=None, version=None):
        self.ops += 1
        return self.store.get(key, default) if self.healthy else default

    def set(self, key, value, timeout=None, version=None):
        self.ops += 1
        if not self.healthy:
            return None  # what django-redis returns when it swallowed an error
        self.store[key] = value
        return True

    def add(self, key, value, timeout=None, version=None):
        self.ops += 1
        if not self.healthy:
            return None
        if key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key, version=None):
        self.ops += 1
        self.store.pop(key, None)


class CircuitBreakerTests(TestCase):
    """A marked-down backend must cost nothing, not a timeout per operation.

    Fail-open already turns a failure into a miss, but a *hung* backend
    charges ``SOCKET_TIMEOUT`` on every operation and a single request makes
    up to nine of them — measured at 4,529 ms per cold request with the
    previous 0.5 s setting. Skipping the doomed call is what makes the
    degraded path cheap.

    Runs against ``REDIS_CACHES`` so the availability tracking is armed;
    under LocMem a ``None`` from ``set`` means success and the breaker
    (correctly) never opens.
    """

    REDIS_CACHES = {"default": {"BACKEND": "django_redis.cache.RedisCache"}}

    def setUp(self):
        cache_utils.reset_backend_availability()
        logging.getLogger("apps.common.cache_utils").setLevel(logging.CRITICAL)

    def tearDown(self):
        cache_utils.reset_backend_availability()
        logging.getLogger("apps.common.cache_utils").setLevel(logging.NOTSET)

    def test_a_swallowed_write_opens_the_breaker(self):
        fake = _CountingCache(healthy=False)
        with override_settings(CACHES=self.REDIS_CACHES), mock.patch.object(
            cache_utils, "_cache", return_value=fake
        ):
            self.assertTrue(cache_utils.cache_backend_available())
            cache_set(make_key("test", "cb"), {"x": 1}, 30)
            self.assertFalse(cache_utils.cache_backend_available())

    def test_helpers_bypass_a_marked_down_backend(self):
        key = make_key("test", "cb-bypass")
        fake = _CountingCache(healthy=False)
        with override_settings(CACHES=self.REDIS_CACHES), mock.patch.object(
            cache_utils, "_cache", return_value=fake
        ):
            cache_set(key, {"x": 1}, 30)  # opens the breaker
            ops = fake.ops
            for _ in range(10):
                self.assertIsNone(cache_get(key))
                self.assertFalse(cache_set(key, {"x": 1}, 30))
                cache_delete(key)
            self.assertEqual(fake.ops, ops, "no operation may reach a down backend")

    def test_locks_are_skipped_while_down(self):
        key = make_key("test", "cb-lock")
        fake = _CountingCache(healthy=False)
        with override_settings(CACHES=self.REDIS_CACHES), mock.patch.object(
            cache_utils, "_cache", return_value=fake
        ):
            cache_set(key, {"x": 1}, 30)  # opens the breaker
            ops = fake.ops
            calls = []

            def compute():
                calls.append(1)
                return {"ok": True}

            self.assertEqual(cache_or_compute(key, compute, 60), {"ok": True})
            with cache_utils.with_lock(key):
                pass
            self.assertEqual(fake.ops, ops, "no lock round trip may be attempted")
            self.assertEqual(calls, [1], "compute still runs — the lock is an optimisation")

    def test_a_healthy_backend_is_always_used(self):
        key = make_key("test", "cb-healthy")
        fake = _CountingCache(healthy=True)
        with override_settings(CACHES=self.REDIS_CACHES), mock.patch.object(
            cache_utils, "_cache", return_value=fake
        ):
            cache_set(key, {"x": 1}, 30)
            self.assertEqual(cache_get(key), {"x": 1})
            self.assertTrue(fake.ops >= 2)
            self.assertTrue(cache_utils.cache_backend_available())

    def test_state_is_dropped_when_the_backend_changes(self):
        """A failure recorded against one backend says nothing about another.

        Guards against a django-redis outage leaking into a later LocMem
        request in the same process — which is exactly what ``override_settings``
        does between tests.
        """
        fake = _CountingCache(healthy=False)
        with override_settings(CACHES=self.REDIS_CACHES), mock.patch.object(
            cache_utils, "_cache", return_value=fake
        ):
            cache_set(make_key("test", "cb-swap"), {"x": 1}, 30)
            self.assertFalse(cache_utils.cache_backend_available())

        locmem = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
        with override_settings(CACHES=locmem):
            self.assertTrue(cache_utils.cache_backend_available())
            self.assertFalse(cache_utils.backend_reports_delivery())
            key = make_key("test", "cb-swap-locmem")
            cache_set(key, {"x": 1}, 30)
            self.assertEqual(cache_get(key), {"x": 1}, "LocMem must not be starved")


class CacheAddTests(TestCase):
    """``cache_add`` is the atomic set-if-absent the pacing and lock paths
    share. Its three-way result is the whole point: ``None`` (backend
    unavailable) must not be confused with ``False`` (someone else won)."""

    def test_first_caller_wins_and_the_second_loses(self):
        key = make_key("test", "add")
        cache_delete(key)
        self.assertTrue(cache_utils.cache_add(key, 1, 30))
        self.assertFalse(cache_utils.cache_add(key, 1, 30))

    def test_a_swallowed_error_returns_none_not_false(self):
        class _Swallowing:
            def add(self, *args, **kwargs):
                return None  # what django-redis returns via IGNORE_EXCEPTIONS

        key = make_key("test", "add-down")
        with override_settings(
            CACHES={"default": {"BACKEND": "django_redis.cache.RedisCache"}}
        ), mock.patch.object(cache_utils, "_cache", return_value=_Swallowing()):
            cache_utils.reset_backend_availability()
            try:
                self.assertIsNone(cache_utils.cache_add(key, 1, 30))
            finally:
                cache_utils.reset_backend_availability()

    def test_a_raising_backend_returns_none(self):
        class _Raising:
            def add(self, *args, **kwargs):
                raise ConnectionError("redis is down")

        key = make_key("test", "add-raise")
        with mock.patch.object(cache_utils, "_cache", return_value=_Raising()):
            self.assertIsNone(cache_utils.cache_add(key, 1, 30))


class FailOpenScopeTests(TestCase):
    """``IGNORE_EXCEPTIONS`` is narrower than its name.

    django-redis's ``omit_exception`` catches ``ConnectionInterrupted`` and
    nothing else, and ``DefaultClient.set`` encodes the value *before* it
    enters its ``try`` block. So a payload the serializer rejects is a plain
    programming error that propagates straight out of ``cache.set`` — the
    fail-open guarantee covers the network, not the code.

    That is safe today only because of a discipline: every production write
    goes through ``cache_utils``, which encodes first and catches, and the
    session path is already JSON-constrained upstream of the cache. Both
    halves are pinned below, because the guarantee is invisible otherwise.
    """

    REDIS_OPTIONS = {
        "CLIENT_CLASS": "django_redis.client.DefaultClient",
        "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
        "IGNORE_EXCEPTIONS": True,
        "SOCKET_CONNECT_TIMEOUT": 0.2,
        "SOCKET_TIMEOUT": 0.2,
    }

    def _redis(self):
        from django_redis.cache import RedisCache

        # The port is never contacted: encoding fails before any I/O.
        return RedisCache("redis://127.0.0.1:63999/0", {"OPTIONS": self.REDIS_OPTIONS})

    def test_a_serialisation_error_is_not_swallowed(self):
        class NotJson:
            pass

        with self.assertRaises(TypeError):
            self._redis().set("probe:weird", {"o": NotJson()}, 30)

    def test_a_connection_error_still_is_swallowed(self):
        """The contrast that defines the boundary."""
        self.assertIsNone(self._redis().get("probe:missing"))

    def test_cache_utils_still_swallows_both(self):
        """The helper layer is what consumers actually call, and it catches
        the encode failure the backend would have propagated."""
        class NotJson:
            def __repr__(self):
                raise ValueError("nope")

        key = make_key("test", "weird")
        with mock.patch.object(
            cache_utils, "_encode", side_effect=TypeError("not serializable")
        ):
            self.assertFalse(cache_set(key, {"o": NotJson()}, 30))
        self.assertIsNone(cache_get(key))

    def test_the_json_serializer_is_configured_not_pickle(self):
        """Pickle would serialize anything and hide this whole class of
        error — at the cost of unreadable values in redis-cli and a
        deserialization surface on every read."""
        from config.settings import _cache_settings

        with mock.patch.dict(os.environ, {"REDIS_URL": "redis://cache.internal:6379/0"}):
            options = _cache_settings()["default"]["OPTIONS"]
        self.assertEqual(
            options["SERIALIZER"], "django_redis.serializers.json.JSONSerializer"
        )
        self.assertNotIn("pickle", options["SERIALIZER"])

    def test_the_session_serializer_rejects_non_json_before_the_cache(self):
        """The second half of the discipline: ``SESSION_SERIALIZER`` is
        Django's JSON one, so a session holding an unserializable object
        fails in ``signing.dumps`` — upstream of any cache write — which is
        why the session path can never be the source of a serializer error
        reaching Redis."""
        from importlib import import_module

        from django.conf import settings
        from django.contrib.sessions.serializers import JSONSerializer

        # The setting is not declared in config/settings.py, so this asserts
        # Django's default is the one in force -- and that nothing has
        # switched it to the pickle serializer.
        self.assertEqual(
            getattr(
                settings,
                "SESSION_SERIALIZER",
                "django.contrib.sessions.serializers.JSONSerializer",
            ),
            "django.contrib.sessions.serializers.JSONSerializer",
        )
        store = import_module(settings.SESSION_ENGINE).SessionStore()
        self.assertIs(store.serializer, JSONSerializer)

        class NotJson:
            pass

        with self.assertRaises(TypeError):
            store.serializer().dumps({"weird": NotJson()})


class CacheAccessDisciplineTests(TestCase):
    """Only ``cache_utils`` may import ``django.core.cache``.

    ``IGNORE_EXCEPTIONS`` does not protect a caller from its own bad payload,
    so a module that writes to the shared cache directly is one programming
    error away from turning a cache write into a 500. Keeping the import
    surface at one file is what makes that hypothetical: this walks the
    source tree, so a new direct consumer fails the suite and gets reviewed
    instead of landing silently.
    """

    ALLOWED = {
        # The fail-open helper layer every production consumer goes through.
        "apps/common/cache_utils.py",
    }
    # Not production code: test helpers may reach the cache directly to set
    # up or assert on state.
    SKIPPED_PARTS = {"tests", "migrations", "__pycache__"}

    def _production_importers(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]  # .../ZaminexB
        importers = []
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if any(part in self.SKIPPED_PARTS for part in path.parts):
                continue
            if path.name == "testing.py" or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a syntax error fails elsewhere
                continue
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                elif isinstance(node, ast.Import):
                    modules = [alias.name or "" for alias in node.names]
                if any("django.core.cache" in m for m in modules):
                    importers.append(relative)
                    break
        return importers

    def test_the_scan_actually_finds_the_helper(self):
        """Guards the guard: an empty or mis-rooted scan would pass silently."""
        self.assertIn("apps/common/cache_utils.py", self._production_importers())

    def test_only_cache_utils_touches_django_core_cache(self):
        self.assertEqual(self._production_importers(), sorted(self.ALLOWED))
