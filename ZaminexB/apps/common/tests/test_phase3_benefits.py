"""Phase 3 — the "zero-code-change" Redis wins, verified.

The roadmap's Phase 3 claims:

* **AI cache on Redis** — once ``REDIS_URL`` is set the AI description cache
  is shared across all workers: each property/consultant hits the LLM once
  per TTL (direct cost saving + consistent, near-instant responses).
* **Global rate limits** — the throttle counters (``ai``, ``password_reset``,
  …) live in the shared cache, so the limits are exact across workers.
* **Fail-open** — a dead cache degrades to a miss, never a 500.

These tests prove the contract without requiring a live Redis: a *shared*
in-memory stand-in plays the role of the Redis server (two distinct backend
handles on one store = two "processes" sharing Redis), and the throttle test
uses two separate connections against the real DRF machinery.
"""

import io
import threading
import time
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, TransactionTestCase

from apps.accounts.models import ConsultantProfile, User
from apps.common import cache_utils
from apps.analytics.ai_service import get_cached_description
from apps.analytics.models import AIInsightCache
from apps.common.models import CompanySettings


class _SharedStore:
    """Stand-in for the Redis server: one store, many backend handles.

    ``backend(name)`` returns a handle bound to the shared store — exactly
    the topology of two Python workers talking to one Redis.
    """

    def __init__(self):
        self.store: dict = {}

    def backend(self, name: str):
        store = self.store

        class _Backend:
            def get(self, key, default=None):
                return store.get(key, default)

            def set(self, key, value, timeout=None):
                store[key] = value
                return True

            def add(self, key, value, timeout=None):
                if key in store:
                    return False
                store[key] = value
                return True

            def delete(self, key):
                store.pop(key, None)

            def clear(self):
                store.clear()

        return _Backend()


def _ai_raw(name: str) -> str:
    return (
        '{"positives":["+1","+2","+3"],"negatives":["-1","-2","-3"],'
        '"summary":"خلاصه برای ' + name + '"}'
    )


class Phase3Base(TestCase):
    """Shared fixtures: AI configured against a mock upstream."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_basics", stdout=io.StringIO())
        cls.admin = User.objects.create_user(
            username="p3-admin", password="pw", role="ADMIN"
        )
        cls.agent = User.objects.create_user(
            username="p3-agent", password="pw", role="AGENT"
        )
        cls.profile = ConsultantProfile.objects.create(
            user=cls.agent, full_name="احمد", branch="مرکزی"
        )

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # LocMem persists across tests within a process
        AIInsightCache.objects.all().delete()
        s = CompanySettings.get_solo()
        s.ai_enabled = True
        s.ai_api_base_url = "https://mock.example/v1"
        s.ai_api_key = "k"
        s.ai_model = "m"
        s.save()

    def _data(self, **extra):
        data = {"name": "احمد", "kpis": {"openTasks": 3}}
        data.update(extra)
        return data


class AICacheCrossProcessTests(Phase3Base):
    """The AI cache is shared between "processes" (workers) via the backend.

    This is the Phase 3 headline: worker B must serve worker A's generated
    description from the shared store — with **zero** new LLM calls.
    """

    def test_second_process_serves_the_shared_cache_without_llm_call(self):
        store = _SharedStore()

        # Worker A: cold cache → generates once.
        with mock.patch.object(cache_utils, "_cache", return_value=store.backend("worker-a")), \
             mock.patch("apps.analytics.ai_service._chat_completion", return_value=_ai_raw("احمد")) as m:
            first = get_cached_description(
                self._data(), entity="consultant", entity_id=self.profile.pk
            )
        self.assertEqual(m.call_count, 1)

        # Remove the DB row so only the *shared cache* can serve worker B —
        # the assertion below is about the cache layer, not the DB fallback.
        AIInsightCache.objects.all().delete()

        # Worker B: a distinct backend handle on the same store (fresh
        # process state), same data → must hit the shared cache.
        with mock.patch.object(cache_utils, "_cache", return_value=store.backend("worker-b")), \
             mock.patch("apps.analytics.ai_service._chat_completion", return_value=_ai_raw("احمد")) as m2:
            second = get_cached_description(
                self._data(), entity="consultant", entity_id=self.profile.pk
            )
        self.assertEqual(m2.call_count, 0)
        self.assertEqual(first, second)

    def test_cache_key_is_versioned_and_json_encoded(self):
        store = _SharedStore()
        with mock.patch.object(cache_utils, "_cache", return_value=store.backend("w")), \
             mock.patch("apps.analytics.ai_service._chat_completion", return_value=_ai_raw("احمد")):
            get_cached_description(
                self._data(), entity="consultant", entity_id=self.profile.pk
            )
        keys = list(store.store.keys())
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].startswith(f"zaminex:{cache_utils.CACHE_VERSION}:ai:desc:"))
        # The stored payload is inspectable JSON text (not pickle bytes).
        raw = store.store[keys[0]]
        self.assertIsInstance(raw, str)
        import json

        decoded = json.loads(raw)
        self.assertIn("fingerprint", decoded)
        self.assertIn("summary", decoded["description"])

    def test_changed_data_still_triggers_regeneration(self):
        store = _SharedStore()
        with mock.patch.object(cache_utils, "_cache", return_value=store.backend("w")), \
             mock.patch("apps.analytics.ai_service._chat_completion", return_value=_ai_raw("احمد")) as m:
            get_cached_description(self._data(), entity="consultant", entity_id=self.profile.pk)
            # New data → new fingerprint → a new generation, not the old cache.
            get_cached_description(
                self._data(kpis={"openTasks": 99}), entity="consultant", entity_id=self.profile.pk
            )
        self.assertEqual(m.call_count, 2)


class AIHerdProtectionTests(TransactionTestCase):
    """Concurrent cold requests for the same record → exactly one LLM call.

    Before Phase 3, N workers missing at the same time all called the model.
    The per-(entity, fingerprint) lock serialises the generation; waiters
    re-check and serve the winner's result.

    ``TransactionTestCase`` (not ``TestCase``) is required: the worker
    threads use their own database connections, which would be deadlocked by
    ``TestCase``'s wrapping, never-committed transaction (the threads'
    ``get_solo()`` would block on the main transaction's uncommitted
    singleton row while the main thread joins the threads).
    """

    serializable_transactions = False  # threads take their own connections

    def setUp(self):
        call_command("seed_basics", stdout=io.StringIO())
        self.agent = User.objects.create_user(
            username="p3-herd-agent", password="pw", role="AGENT"
        )
        self.profile = ConsultantProfile.objects.create(
            user=self.agent, full_name="احمد", branch="مرکزی"
        )
        s = CompanySettings.get_solo()
        s.ai_enabled = True
        s.ai_api_base_url = "https://mock.example/v1"
        s.ai_api_key = "k"
        s.ai_model = "m"
        s.save()

    def _data(self):
        return {"name": "احمد", "kpis": {"openTasks": 3}}

    def test_concurrent_cold_requests_generate_exactly_once(self):
        calls: list = []
        results: list = []
        errors: list = []
        barrier = threading.Barrier(5)

        def slow_chat_completion(system, user):
            calls.append(time.monotonic())
            time.sleep(0.3)  # simulate the LLM round trip
            return _ai_raw("احمد")

        def worker():
            try:
                barrier.wait()
                results.append(
                    get_cached_description(
                        self._data(), entity="consultant", entity_id=self.profile.pk
                    )
                )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                # Each thread holds its own DB connection; close it so the
                # test process does not leak connections.
                from django.db import connection

                connection.close()

        with mock.patch("apps.analytics.ai_service._chat_completion", side_effect=slow_chat_completion):
            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1, "the herd must pay for exactly one model call")
        self.assertEqual(len(results), 5)
        self.assertTrue(all(r == results[0] for r in results))
        # And the result is stored for future requests.
        self.assertEqual(
            AIInsightCache.objects.filter(entity="consultant", entity_id=self.profile.pk).count(),
            1,
        )


class AIFailOpenTests(Phase3Base):
    """A dead cache backend degrades to the DB fallback — never a crash."""

    class _DeadCache:
        def __getattr__(self, _name):
            raise ConnectionError("redis is down")

    def test_dead_cache_serves_from_the_db_without_llm_call(self):
        # Warm the DB row with a live cache first (one model call).
        with mock.patch("apps.analytics.ai_service._chat_completion", return_value=_ai_raw("احمد")) as m:
            first = get_cached_description(
                self._data(), entity="consultant", entity_id=self.profile.pk
            )
        self.assertEqual(m.call_count, 1)

        # Cache is now dead: the DB row must serve the request.
        with mock.patch.object(cache_utils, "_cache", return_value=self._DeadCache()), \
             mock.patch("apps.analytics.ai_service._chat_completion", return_value=_ai_raw("احمد")) as m2:
            second = get_cached_description(
                self._data(), entity="consultant", entity_id=self.profile.pk
            )
        self.assertEqual(m2.call_count, 0)
        self.assertEqual(first, second)


class GlobalThrottleTests(TestCase):
    """Throttle counters are shared — across connections, in the cache.

    DRF throttles read/write their counters through the default cache
    backend, so with ``REDIS_URL`` set they are exact across workers. The
    test asserts the *sharing*: two separate connections draw from one
    counter (here LocMem, the in-process stand-in for the shared backend).
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # throttle counters live in the cache; start clean

    def test_password_reset_limit_is_shared_across_two_connections(self):
        c1 = Client()
        c2 = Client()
        body = {"username": "ghost-user-p3"}  # nonexistent → no side effects

        # 5/hour anon limit: five requests alternate between the two
        # connections and all succeed — the counter is shared, not per-connection.
        for i in range(5):
            res = (c1 if i % 2 == 0 else c2).post(
                "/common/api/password-reset-request/", body, format="json"
            )
            self.assertEqual(res.status_code, 200, f"request {i + 1} should pass")

        # The sixth — from either connection — is throttled.
        res = c1.post("/common/api/password-reset-request/", body, format="json")
        self.assertEqual(res.status_code, 429)


class WithLockTests(TestCase):
    """The cache_utils.with_lock primitive itself."""

    def test_lock_is_acquired_and_released(self):
        from django.core.cache import cache

        key = cache_utils.make_key("test", "lock-basic")
        with cache_utils.with_lock(key) as owned:
            self.assertTrue(owned)
            self.assertIsNotNone(cache.get(key))
        self.assertIsNone(cache.get(key))

    def test_second_lock_waits_for_the_first_holder(self):
        key = cache_utils.make_key("test", "lock-wait")
        order: list = []

        def first_holder():
            with cache_utils.with_lock(key, wait=2):
                order.append("first-in")
                time.sleep(0.3)
                order.append("first-out")

        thread = threading.Thread(target=first_holder)
        thread.start()
        time.sleep(0.1)  # let the first holder take the lock

        t0 = time.monotonic()
        with cache_utils.with_lock(key, wait=2) as owned:
            order.append("second-in")
        waited = time.monotonic() - t0
        thread.join()

        # The second lock could not proceed while the first held it.
        self.assertGreaterEqual(waited, 0.15)
        self.assertEqual(order, ["first-in", "first-out", "second-in"])

    def test_dead_backend_is_fail_open(self):
        class _Dead:
            def __getattr__(self, _name):
                raise ConnectionError("down")

        key = cache_utils.make_key("test", "lock-dead")
        with mock.patch.object(cache_utils, "_cache", return_value=_Dead()):
            with cache_utils.with_lock(key) as owned:
                # No protection available → proceed (work must not be blocked).
                self.assertFalse(owned)
        # And no crash on exit.
