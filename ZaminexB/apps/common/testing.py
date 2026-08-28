"""Shared test helpers for the Phase 5+ cache layer."""

from django.core.cache import cache


class CacheClearingMixin:
    """Reset the default cache around each test.

    Phase 5 added response-level caches (reference data, list COUNTs, poll
    counts). Django's ``TestCase`` rolls back the *database* between tests
    but not the cache, so a payload cached by one test would otherwise be
    served to the next test's fresh data (a stale global ``catalog`` or
    ``location-tree`` key, or a schema cached under a shared ``setUpTestData``
    PK). Clearing before and after each test restores per-test isolation.
    """

    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()
