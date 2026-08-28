"""Phase 6 — sessions on the ``cached_db`` engine (the cache in front of the
``django_session`` table), with the roadmap acceptance:

* a cache flush must **not** kill a logged-in session (the row stays in the
  table; the next read reloads it), and
* the login lockout must behave exactly as before.

Plus the project-wide guarantees the phase inherits: fail-open (a dead cache
presents as "always a miss / no-op writes" under the Phase-2
``IGNORE_EXCEPTIONS=True`` config, and login, requests and logout must all
keep working — served from the table), and the per-request win (a warm
request saves the session SELECT — one fewer round trip against the
session table).

The roadmap's *optional* LoginAttempt → atomic Redis counter stays deferred
by design: it is gated on a real brute-force/scale trigger, and the current
DB lockout (one row per username, touched only by *failed* logins, behind the
Phase-3 global 10/min login throttle) is sufficient at the current scale.
"""

from unittest import mock

from django.contrib.sessions.models import Session
from django.core.cache import cache as django_cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import LoginAttempt, User, UserRole
from apps.accounts.tests import extract_login_errors
from apps.common.testing import CacheClearingMixin


class _FailOpenDeadCache:
    """What a dead backend looks like to callers under IGNORE_EXCEPTIONS=True:
    every read is a miss, every write is a silent no-op, and nothing raises.
    (A *raising* cache would be the wrong simulation — the production backend
    swallows errors by design, so the session store must survive exactly this
    shape.)"""

    def get(self, key, default=None, version=None):
        return default

    def set(self, key, value, timeout=None, version=None):
        return True

    def add(self, key, value, timeout=None, version=None):
        return False

    def delete(self, key, version=None):
        return None

    def has_key(self, key, version=None):
        return False

    def __contains__(self, key):
        return False

    def clear(self, timeout=0):
        return None

    def expire(self, key, timeout=None, version=None):
        return None


def _patch_session_cache(cache):
    """Point the session store's cache lookup (caches["default"]) at ``cache``
    — surgical: only ``django.contrib.sessions.backends.cached_db`` is
    affected, the app caches and throttles keep the real backend."""
    return mock.patch(
        "django.contrib.sessions.backends.cached_db.caches",
        mock.MagicMock(**{"__getitem__.return_value": cache}),
    )


def _session_queries(captured):
    return [q for q in captured if "django_session" in q["sql"]]


class Phase6Base(CacheClearingMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="p6-admin", password="p6-pass-123", role=UserRole.ADMIN
        )
        self.client = Client(SERVER_NAME="localhost")

    def _login(self, password="p6-pass-123", username="p6-admin"):
        return self.client.post(
            "/accounts/login/", {"username": username, "password": password}
        )


class SessionSurvivesFlushTests(Phase6Base):
    """Roadmap acceptance #1: flush the cache — the session must live on."""

    def test_session_is_persisted_in_the_table(self):
        res = self._login()
        self.assertEqual(res.status_code, 302)
        key = self.client.session.session_key
        self.assertTrue(key)
        self.assertTrue(Session.objects.filter(session_key=key).exists())

    def test_session_survives_a_cache_flush(self):
        res = self._login()
        self.assertEqual(res.status_code, 302)
        key = self.client.session.session_key

        # Simulate a Redis flush / a cache version bump: everything cached is
        # gone, the django_session table is untouched.
        django_cache.clear()
        self.assertFalse(django_cache.get(f"django.contrib.sessions.cached_db{key}"))

        # The very next request must re-authenticate from the table.
        res = self.client.get("/tickets/api/unread-count/")
        self.assertEqual(res.status_code, 200)
        # …and the session was repopulated in the cache for the next request.
        self.assertTrue(
            django_cache.get(f"django.contrib.sessions.cached_db{key}") is not None
        )


class WarmReadSkipsTheSessionTableTests(Phase6Base):
    """The phase's per-request win: a warm request saves the session SELECT —
    one fewer round trip against the session table (the write still lands in
    the table, which is what makes flush-survival possible)."""

    def test_warm_request_does_one_fewer_session_table_query_than_cold(self):
        self._login()
        django_cache.clear()  # cold start for the measurement window

        with CaptureQueriesContext(connection) as cold:
            res1 = self.client.get("/tickets/api/unread-count/")
        with CaptureQueriesContext(connection) as warm:
            res2 = self.client.get("/tickets/api/unread-count/")
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)

        cold_session = _session_queries(cold.captured_queries)
        warm_session = _session_queries(warm.captured_queries)
        # Cold: the store must SELECT the session row (miss) — warm: the read
        # is a cache hit, so exactly one fewer session-table query.
        self.assertGreaterEqual(len(cold_session), 1)
        self.assertEqual(len(cold_session) - len(warm_session), 1)


class FailOpenTests(Phase6Base):
    """A dead cache (fail-open) must never break login, an authenticated
    request, or logout — the session degrades to the plain DB engine."""

    def test_login_requests_and_logout_work_with_a_dead_cache(self):
        dead = _FailOpenDeadCache()
        with _patch_session_cache(dead):
            res = self._login()
            self.assertEqual(res.status_code, 302)  # login: DB-backed save
            key = self.client.session.session_key
            self.assertTrue(
                Session.objects.filter(session_key=key).exists()
            )  # durable in the table even with a dead cache

            res = self.client.get("/tickets/api/unread-count/")
            self.assertEqual(res.status_code, 200)  # reloaded from the table

            res = self.client.post("/accounts/logout/")
            self.assertEqual(res.status_code, 302)  # delete path: no raise

            res = self.client.get("/tickets/api/unread-count/")
            self.assertEqual(res.status_code, 403)  # logged out, cleanly

    def test_logout_clears_the_session_from_table_and_cache(self):
        self._login()
        key = self.client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=key).exists())

        res = self.client.post("/accounts/logout/")
        self.assertEqual(res.status_code, 302)

        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertIsNone(
            django_cache.get(f"django.contrib.sessions.cached_db{key}")
        )
        res = self.client.get("/tickets/api/unread-count/")
        self.assertEqual(res.status_code, 403)


class LockoutStillCorrectTests(Phase6Base):
    """Roadmap acceptance #2: with the session engine changed, the login
    lockout must behave exactly as before — five failures lock the username,
    and even a correct password is refused while the lock is active."""

    def test_lockout_blocks_even_the_correct_password(self):
        for _ in range(5):
            res = self._login(password="wrong-pass-1")
            self.assertEqual(res.status_code, 200)  # re-rendered login page
        self.assertTrue(
            LoginAttempt.objects.get(username="p6-admin").locked_until is not None
        )

        # Correct password, but the lock is active → refused.
        res = self._login()
        self.assertEqual(res.status_code, 200)
        errors = extract_login_errors(res.content.decode("utf-8"))
        self.assertTrue(
            any("مسدود" in msg for msg in errors.get("__all__", []))
        )
