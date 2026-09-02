"""Tests for the geocoding proxy (`apps/common/geocode.py` + `GeocodeView`).

The endpoint exists because the browser cannot call a public geocoder: the
app's CSP allows ``connect-src 'self'`` plus the tile host, so a direct
``fetch`` to the upstream was blocked and every place search reported
«نتیجه‌ای یافت نشد». What is under test here is therefore the contract that
replaces it:

* the **normaliser** folds Persian spelling variants to one key — and must
  agree with its TypeScript twin in
  ``ZaminexF/src/shared/lib/iranLocations.ts``, which is asserted against the
  same fixed table of expectations in both suites;
* the **view** distinguishes "no such place" (200 + ``[]``) from "the
  geocoder is down" (503), which is what lets the UI say so;
* results are **cached**, spelling variants share one entry, and a miss is
  cached far less than a hit;
* only a strictly validated ``viewbox`` is forwarded upstream.

The upstream is always mocked — this sandbox has no outbound HTTPS, and a
test suite must not depend on a public service's availability anyway.
"""

import json
import urllib.error
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import UserRole
from apps.common.geocode import (
    GeocodeUnavailable,
    clean_viewbox,
    geocode,
    normalize_place_key,
)
from django.contrib.auth import get_user_model

User = get_user_model()

URL = "/common/api/geocode/"

# One row per assertion in the twin vitest suite
# (ZaminexF/src/shared/lib/iranLocations.test.ts). The expected keys are the
# contract; changing one side without the other fails a test rather than
# silently splitting the cache.
PARITY_CASES = [
    ("خرم‌آباد", "خرماباد"),
    ("خرم اباد", "خرماباد"),
    ("بندر  عباس", "بندرعباس"),
    ("آبادان", "ابادان"),
    ("قائم‌شهر", "قائمشهر"),
    ("قائم شهر", "قائمشهر"),
    ("مشهد", "مشهد"),
    ("", ""),
    (None, ""),
    ("   ", ""),
]


class _FakeResponse:
    """The ``urlopen`` context manager the proxy reads exactly once."""

    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _upstream(rows):
    """Patch target returning ``rows`` (already-parsed JSON) to the proxy."""
    return mock.patch(
        "apps.common.geocode.urllib.request.urlopen",
        return_value=_FakeResponse(json.dumps(rows)),
    )


def _raise(exc):
    return mock.patch("apps.common.geocode.urllib.request.urlopen", side_effect=exc)


# Fast tests: no pacing sleep. Pacing itself is asserted separately.
@override_settings(GEOCODE_PACING_SECONDS=0)
class GeocodeTestBase(TestCase):
    def setUp(self):
        super().setUp()
        # LocMemCache is NOT cleared between tests, so without this an earlier
        # test's cached "ساری" makes a later one pass without ever calling the
        # upstream. Order-independence is the point, not a nicety.
        cache.clear()
        self.user = User.objects.create_user(
            username=f"geo-{self._testMethodName}", password="pw-secret-1", role=UserRole.AGENT
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


# ---------------------------------------------------------------------------
#  Normaliser
# ---------------------------------------------------------------------------


class NormalizePlaceKeyTests(TestCase):
    def test_parity_with_the_typescript_implementation(self):
        for raw, expected in PARITY_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_place_key(raw), expected)

    def test_zwnj_and_a_plain_space_are_the_same_word(self):
        # The bug this exists to prevent: the city table stores the ZWNJ form
        # while operators type a space, and a key that kept either one would
        # miss on the other with no error anywhere.
        self.assertEqual(normalize_place_key("خرم‌آباد"), normalize_place_key("خرم اباد"))

    def test_arabic_letters_fold_to_their_persian_forms(self):
        self.assertEqual(normalize_place_key("مشهد"), normalize_place_key("مشهد"))
        # Arabic YEH (ي) and KAF (ك) vs Persian ی and ک.
        self.assertEqual(normalize_place_key("يزد"), normalize_place_key("یزد"))
        self.assertEqual(normalize_place_key("كرمان"), normalize_place_key("کرمان"))
        # TEH MARBUTA and ALEF MAKSURA.
        self.assertEqual(normalize_place_key("فاطمه"), normalize_place_key("فاطمه"))
        self.assertEqual(normalize_place_key("موسي"), normalize_place_key("موسی"))

    def test_alef_variants_fold_to_a_plain_alef(self):
        # "آبادان" is written with آ (ALEF WITH MADDA) or with a plain ا
        # depending on who typed it.
        self.assertEqual(normalize_place_key("آبادان"), normalize_place_key("ابادان"))
        self.assertEqual(normalize_place_key("أحمد"), normalize_place_key("احمد"))

    def test_arabic_diacritics_and_tatweel_are_dropped(self):
        self.assertEqual(normalize_place_key("تَهــران"), normalize_place_key("تهران"))

    def test_letter_order_is_never_changed(self):
        # This is a canonicalisation, not a fuzzy match: two genuinely
        # different names must never share a key.
        self.assertNotEqual(normalize_place_key("تهران"), normalize_place_key("تهرانر"))
        self.assertNotEqual(normalize_place_key("کرج"), normalize_place_key("گرگ"))

    def test_accepts_non_strings(self):
        self.assertEqual(normalize_place_key(12), "12")


class CleanViewboxTests(TestCase):
    def test_four_numbers_are_formatted_to_six_decimals(self):
        self.assertEqual(clean_viewbox("52.1, 36.9,54.4,35.9"), "52.100000,36.900000,54.400000,35.900000")

    def test_wrong_arity_is_rejected(self):
        self.assertIsNone(clean_viewbox("52.1,36.9,54.4"))
        self.assertIsNone(clean_viewbox("52.1,36.9,54.4,35.9,1"))

    def test_non_numeric_is_rejected(self):
        self.assertIsNone(clean_viewbox("52.1,36.9,54.4,abc"))
        self.assertIsNone(clean_viewbox("52.1,36.9,54.4,35.9&countrycodes=us"))

    def test_non_finite_is_rejected(self):
        self.assertIsNone(clean_viewbox("52.1,36.9,inf,35.9"))
        self.assertIsNone(clean_viewbox("52.1,36.9,nan,35.9"))

    def test_empty_is_none_not_invalid(self):
        self.assertIsNone(clean_viewbox(None))
        self.assertIsNone(clean_viewbox(""))


# ---------------------------------------------------------------------------
#  The proxy core
# ---------------------------------------------------------------------------


class GeocodeFunctionTests(GeocodeTestBase):
    def test_parses_lat_lon_as_floats_and_keeps_the_address(self):
        with _upstream(
            [
                {
                    "lat": "36.5633",
                    "lon": "53.0601",
                    "display_name": "ساری، مازندران",
                    "address": {"city": "ساری", "state": "مازندران"},
                }
            ]
        ):
            results = geocode("ساری, مازندران")

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0]["lat"], float)
        self.assertIsInstance(results[0]["lon"], float)
        self.assertEqual(results[0]["lat"], 36.5633)
        self.assertEqual(results[0]["address"]["state"], "مازندران")
        self.assertEqual(results[0]["displayName"], "ساری، مازندران")

    def test_asks_for_persian_labels_and_restricts_to_iran(self):
        # Item 6 of the agreed scope: without Accept-Language the upstream
        # answers in Latin script and the Persian UI shows "Sari".
        with _upstream([]) as patched:
            geocode("ساری")
        sent = patched.call_args[0][0]
        query = sent.full_url
        self.assertIn("accept-language=fa", query)
        self.assertIn("countrycodes=ir", query)
        self.assertEqual(sent.get_header("Accept-language"), "fa")
        self.assertTrue(sent.get_header("User-agent"))

    def test_viewbox_is_forwarded_and_bounded_only_when_asked(self):
        with _upstream([]) as patched:
            geocode("ساری", "52.1,36.9,54.4,35.9", bounded=True)
        self.assertIn("bounded=1", patched.call_args[0][0].full_url)
        self.assertIn("viewbox=52.1%2C36.9%2C54.4%2C35.9", patched.call_args[0][0].full_url)

        with _upstream([]) as patched:
            geocode("ساری", "52.1,36.9,54.4,35.9", bounded=False)
        self.assertNotIn("bounded=1", patched.call_args[0][0].full_url)

    def test_second_call_is_served_from_cache(self):
        with _upstream([{"lat": "36.5", "lon": "53.0"}]) as patched:
            first = geocode("ساری")
            second = geocode("ساری")
        self.assertEqual(first, second)
        self.assertEqual(patched.call_count, 1)

    def test_spelling_variants_share_one_cache_entry(self):
        with _upstream([{"lat": "33.4878", "lon": "48.3558"}]) as patched:
            geocode("خرم‌آباد")
            geocode("خرم اباد")
            geocode("خرم اباد ")
        self.assertEqual(patched.call_count, 1)

    @override_settings(GEOCODE_CACHE_TTL=1234, GEOCODE_NEGATIVE_CACHE_TTL=56)
    def test_a_miss_is_cached_for_far_less_than_a_hit(self):
        # OpenStreetMap's coverage of Iran improves, so a stale "not found"
        # must not outlive the fix the way a confirmed coordinate may.
        with mock.patch("apps.common.geocode.cache_utils.cache_set") as cache_set:
            with _upstream([{"lat": "1", "lon": "2"}]):
                geocode("ساری")
            with _upstream([]):
                geocode("تهران-ناموجود")

        self.assertEqual(cache_set.call_count, 2)
        ttls = sorted(call.args[2] for call in cache_set.call_args_list)
        self.assertEqual(ttls, [56, 1234])

    def test_upstream_failure_raises_geocode_unavailable(self):
        for exc in (
            urllib.error.URLError("no route"),
            TimeoutError("too slow"),
            OSError("reset"),
        ):
            with self.subTest(exc=type(exc).__name__):
                with _raise(exc):
                    with self.assertRaises(GeocodeUnavailable):
                        geocode(f"ساری-{type(exc).__name__}")

    def test_a_non_json_body_is_unavailable_not_a_miss(self):
        with mock.patch(
            "apps.common.geocode.urllib.request.urlopen",
            return_value=_FakeResponse("<html>429 Too Many Requests</html>"),
        ):
            with self.assertRaises(GeocodeUnavailable):
                geocode("ساری-html")

    def test_an_unexpected_shape_is_unavailable(self):
        with _upstream({"error": "rate limited"}):
            with self.assertRaises(GeocodeUnavailable):
                geocode("ساری-shape")

    def test_rows_without_coordinates_are_dropped(self):
        with _upstream(
            [{"lat": None, "lon": "53.0"}, {"lat": "oops", "lon": "53.0"}, {"lat": "36.5", "lon": "53.0"}]
        ):
            results = geocode("ساری-rows")
        self.assertEqual(len(results), 1)

    @override_settings(GEOCODE_PACING_SECONDS=0.2)
    def test_pacing_reserves_a_slot_in_the_shared_cache(self):
        # The class-wide 0 disables pacing entirely (a zero-width window is
        # never entered), so this test asks for a real interval.
        from apps.common import cache_utils
        from apps.common.geocode import _pace

        slot = cache_utils.make_key("geocode", "pace")
        cache_utils.cache_delete(slot)
        _pace()
        self.assertIsNotNone(cache_utils.cache_get(slot))

    @override_settings(GEOCODE_PACING_SECONDS=0)
    def test_pacing_is_disabled_at_zero(self):
        from apps.common import cache_utils
        from apps.common.geocode import _pace

        slot = cache_utils.make_key("geocode", "pace")
        cache_utils.cache_delete(slot)
        _pace()
        self.assertIsNone(cache_utils.cache_get(slot))

    @override_settings(GEOCODE_PACING_SECONDS=0.15)
    def test_a_busy_slot_is_waited_out_then_sent_anyway(self):
        # Bounded on purpose: stalling a user's search is worse than letting
        # the upstream answer an over-eager client with a 429.
        import time as _time

        from apps.common import cache_utils
        from apps.common.geocode import _pace

        slot = cache_utils.make_key("geocode", "pace")
        cache_utils.cache_delete(slot)
        _pace()  # takes the slot
        started = _time.monotonic()
        _pace()  # must wait it out, then return rather than block forever
        elapsed = _time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLess(elapsed, 3.0)


# ---------------------------------------------------------------------------
#  The HTTP contract
# ---------------------------------------------------------------------------


class GeocodeViewTests(GeocodeTestBase):
    def test_anonymous_caller_is_rejected(self):
        client = APIClient()
        with _upstream([{"lat": "36.5", "lon": "53.0"}]):
            response = client.get(URL, {"q": "ساری"})
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_hit_returns_a_bare_array(self):
        # The Nominatim shape, on purpose: the browser parser barely changes.
        with _upstream([{"lat": "36.5633", "lon": "53.0601", "address": {"city": "ساری"}}]):
            response = self.client.get(URL, {"q": "ساری"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["lat"], 36.5633)

    def test_no_match_is_200_with_an_empty_array(self):
        with _upstream([]):
            response = self.client.get(URL, {"q": "جای‌ناموجود"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

    def test_unreachable_upstream_is_503_not_404(self):
        # The distinction the UI needs: 503 means "we could not look it up",
        # which must not be reported to the operator as "no such place".
        with _raise(urllib.error.URLError("no route")):
            response = self.client.get(URL, {"q": "ساری"})
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("در دسترس نیست", response.json()["detail"])

    def test_empty_query_is_400_with_a_persian_message(self):
        response = self.client.get(URL, {"q": "   "})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(response.json()["detail"])

    def test_missing_query_is_400(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(GEOCODE_MAX_QUERY_LENGTH=12)
    def test_overlong_query_is_400(self):
        response = self.client.get(URL, {"q": "س" * 13})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_viewbox_is_400_and_never_forwarded(self):
        with _upstream([]) as patched:
            response = self.client.get(
                URL, {"q": "ساری", "viewbox": "52.1,36.9,54.4,35.9&countrycodes=us"}
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(patched.call_count, 0)

    def test_valid_viewbox_is_normalised_and_forwarded(self):
        with _upstream([]) as patched:
            response = self.client.get(
                URL, {"q": "ساری", "viewbox": " 52.1, 36.9 ,54.4,35.9 ", "bounded": "1"}
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sent = patched.call_args[0][0].full_url
        self.assertIn("viewbox=52.100000%2C36.900000%2C54.400000%2C35.900000", sent)
        self.assertIn("bounded=1", sent)

    def test_bounded_accepts_the_common_truthy_spellings(self):
        for value in ("1", "true", "yes"):
            with self.subTest(value=value):
                with _upstream([]) as patched:
                    self.client.get(URL, {"q": f"ساری-{value}", "viewbox": "1,2,3,4", "bounded": value})
                self.assertIn("bounded=1", patched.call_args[0][0].full_url)

    def test_bounded_ignores_anything_else(self):
        with _upstream([]) as patched:
            self.client.get(URL, {"q": "ساری-x", "viewbox": "1,2,3,4", "bounded": "on"})
        self.assertNotIn("bounded=1", patched.call_args[0][0].full_url)

    def test_the_same_query_is_served_from_cache_across_requests(self):
        with _upstream([{"lat": "36.5", "lon": "53.0"}]) as patched:
            self.client.get(URL, {"q": "ساری"})
            self.client.get(URL, {"q": "ساری"})
        self.assertEqual(patched.call_count, 1)

    def test_throttled_with_its_own_scope(self):
        from apps.common.views import GeocodeView

        self.assertIn("ScopedRateThrottle", [c.__name__ for c in GeocodeView.throttle_classes])
        self.assertEqual(GeocodeView.throttle_scope, "geocode")

    def test_the_endpoint_is_registered_under_the_common_api_prefix(self):
        from django.urls import reverse

        self.assertEqual(reverse("geocode"), URL)
