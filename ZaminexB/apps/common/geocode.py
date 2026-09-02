"""Server-side geocoding proxy for the map's place search.

Why this lives on the server
----------------------------
The map picker used to call ``https://nominatim.openstreetmap.org`` straight
from the browser. Two problems with that:

* The app's own ``Content-Security-Policy`` (``apps/common/middleware.py``)
  allows ``connect-src 'self'`` plus the OpenStreetMap **tile** host only, so
  the browser blocked every geocode request before it left the machine. The
  failure was silent — the caller could not tell "blocked" from "no match" —
  and the picker reported «نتیجه‌ای یافت نشد» for every query.
* Calling a public, rate-limited service from every browser means the usage
  policy (≈1 request/second, a descriptive ``User-Agent``) is enforced by
  nothing, and there is no place to cache a result.

Routing through here gives one egress point that caches, paces itself and
identifies itself — and because the browser now only talks to its own origin,
no third-party host has to be added to the CSP.

``GEOCODE_UPSTREAM`` is a setting rather than a constant so a self-hosted
Nominatim can be pointed at with an environment variable and no code change;
that is also what makes a fully offline deployment possible.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from . import cache_utils

__all__ = [
    "GeocodeUnavailable",
    "normalize_place_key",
    "geocode",
]


class GeocodeUnavailable(Exception):
    """The upstream geocoder could not be reached or answered badly.

    Deliberately distinct from "no match": a caller that conflates the two
    tells the operator the place does not exist when in fact nothing was ever
    asked. The view turns this into ``503`` and an empty result list into
    ``200 []``, which is what lets the UI say «جستجوی مکان در دسترس نیست»
    instead of «نتیجه‌ای یافت نشد».
    """


# ---------------------------------------------------------------------------
#  Query normalisation
# ---------------------------------------------------------------------------

# Persian is routinely typed with any of several equivalent spellings, and the
# reference data (city / district display names) is free text an administrator
# edits. Without folding those variants together, "خرم‌آباد" (with a ZWNJ) and
# "خرم اباد" (with a plain space) become two different cache keys and two
# different lookups in a static coordinate table — the cache silently misses
# and a table hit silently fails, with no error anywhere.
_EQUIVALENT_LETTERS = str.maketrans(
    {
        "\u064a": "\u06cc",  # Arabic YEH    → Persian YEH
        "\u0643": "\u06a9",  # Arabic KAF    → Persian KEHEH
        "\u0629": "\u0647",  # TEH MARBUTA   → HEH
        "\u0649": "\u06cc",  # ALEF MAKSURA  → Persian YEH
        "\u0622": "\u0627",  # ALEF W/ MADDA → ALEF
        "\u0623": "\u0627",  # ALEF W/ HAMZA ABOVE → ALEF
        "\u0625": "\u0627",  # ALEF W/ HAMZA BELOW → ALEF
        "\u0671": "\u0627",  # ALEF WASLA    → ALEF
    }
)

# ZWNJ / ZWJ / Arabic tatweel carry no matching value; Arabic diacritics are
# decorative. Dropping them is what makes the two spellings above collide.
_IGNORABLE = ("\u200c", "\u200d", "\u0640")


def normalize_place_key(value: str | None) -> str:
    """Fold a Persian place name to a canonical comparison key.

    Only characters that are *spelling* variants are folded — letter order is
    never touched, so this is not a fuzzy match and cannot merge two genuinely
    different names.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    for ch in _IGNORABLE:
        text = text.replace(ch, "")
    text = text.translate(_EQUIVALENT_LETTERS)
    # Strip Arabic diacritics (FATHATAN .. SUKUN) and the Arabic percent-ish
    # marks that occasionally slip in from copied addresses.
    text = "".join(
        ch for ch in text if not ("\u064b" <= ch <= "\u0652")
    )
    # Whitespace carries no identity here, so none of it survives — not even a
    # plain space. Removing the ZWNJ alone is not enough: the city table holds
    # "خرم‌آباد" (ZWNJ) while operators type "خرم اباد" (space), and a key that
    # keeps one of the two would silently miss on the other.
    #
    # This must stay character-for-character in step with normalizePlaceKey()
    # in ZaminexF/src/shared/lib/iranLocations.ts: both sides compute the same
    # key, the server for its cache and the browser for its static tables.
    return re.sub(r"[\s\u200b-\u200f\u2060\ufeff]", "", text)


def clean_viewbox(raw) -> str | None:
    """Validate a Nominatim ``viewbox`` (west,north,east,south) strictly.

    This is the one caller-supplied value that reaches the upstream, so it is
    parsed rather than passed through: anything that is not exactly four
    finite numbers is rejected instead of being smuggled into the query
    string. ``None`` (absent) is legitimate and means "no constraint".

    Returns the canonical ``"%.6f,%.6f,%.6f,%.6f"`` form, which also keeps
    equivalent spellings of one box on one cache entry.
    """
    if not raw:
        return None
    parts = [p.strip() for p in str(raw).split(",")]
    if len(parts) != 4:
        return None
    numbers = []
    for part in parts:
        try:
            numbers.append(float(part))
        except ValueError:
            return None
    if any(n != n or n in (float("inf"), float("-inf")) for n in numbers):
        return None
    return ",".join(f"{n:.6f}" for n in numbers)


# ---------------------------------------------------------------------------
#  Upstream call
# ---------------------------------------------------------------------------


def _upstream_url() -> str:
    return getattr(
        settings, "GEOCODE_UPSTREAM", "https://nominatim.openstreetmap.org/search"
    )


def _user_agent() -> str:
    return getattr(
        settings,
        "GEOCODE_USER_AGENT",
        # Mirrors GEOCODE_USER_AGENT in config/settings.py; only reached if a
        # deployment removes that setting.
        "Zaminex-CRM/1.0 (+self-hosted real-estate CRM; geocode proxy)",
    )


def _pace() -> None:
    """Keep the *server's* request rate inside the upstream's usage policy.

    The public Nominatim instance allows roughly one request per second per
    client. A token in the shared cache makes that budget global across
    workers when Redis is present; on the in-process fallback it degrades to
    per-process pacing, which is still an improvement over the previous
    behaviour of every browser pacing itself independently.

    The wait is bounded: giving up and sending anyway is better than stalling
    a user's search, and the upstream answers an over-eager client with a 429
    that surfaces as :class:`GeocodeUnavailable` rather than corrupting data.

    ``GEOCODE_PACING_SECONDS = 0`` disables pacing (a zero-width window is
    never entered), which is what a deployment pointed at a self-hosted
    Nominatim of its own wants.
    """
    seconds = float(getattr(settings, "GEOCODE_PACING_SECONDS", 1.1))
    slot = cache_utils.make_key("geocode", "pace")
    deadline = time.monotonic() + seconds * 3
    while time.monotonic() < deadline:
        try:
            if cache_utils.cache_get(slot) is None:
                # ``cache_set`` is fail-open; if the backend is down the pace
                # is simply not enforced, which is the intended degradation.
                cache_utils.cache_set(slot, 1, seconds)
                return
        except Exception:  # pragma: no cover - defensive, helpers are fail-open
            return
        time.sleep(0.05)


def _fetch_upstream(query: str, viewbox: str | None, bounded: bool) -> list[dict]:
    """One geocode request. Raises :class:`GeocodeUnavailable` on any failure."""
    params = {
        "q": query,
        "countrycodes": "ir",
        "format": "jsonv2",
        "limit": str(getattr(settings, "GEOCODE_LIMIT", 1)),
        # Without this the upstream answers in its default language and the
        # Persian UI ends up displaying Latin-script labels for Iranian places.
        "accept-language": "fa",
    }
    if viewbox:
        params["viewbox"] = viewbox
        if bounded:
            params["bounded"] = "1"

    url = f"{_upstream_url().rstrip('/')}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _user_agent(),
            "Accept": "application/json",
            "Accept-Language": "fa",
        },
    )
    timeout = float(getattr(settings, "GEOCODE_TIMEOUT", 8))

    _pace()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise GeocodeUnavailable(f"upstream geocoder unreachable: {exc}") from exc

    try:
        data = json.loads(body)
    except ValueError as exc:
        raise GeocodeUnavailable(f"upstream returned a non-JSON body: {exc}") from exc
    if not isinstance(data, list):
        raise GeocodeUnavailable("upstream returned an unexpected shape")

    results = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row.get("lat"))
            lon = float(row.get("lon"))
        except (TypeError, ValueError):
            continue
        address = row.get("address")
        results.append(
            {
                "lat": lat,
                "lon": lon,
                # Kept because the caller's acceptance rule reads the parent
                # names out of it to reject a hit from the wrong province.
                "address": address if isinstance(address, dict) else {},
                "displayName": row.get("display_name") or "",
            }
        )
    return results


# ---------------------------------------------------------------------------
#  Public entry point
# ---------------------------------------------------------------------------


def geocode(query: str, viewbox: str | None = None, bounded: bool = False) -> list[dict]:
    """Resolve ``query`` to at most ``GEOCODE_LIMIT`` hits, cache-first.

    Place coordinates are effectively permanent, so a hit is cached for
    ``GEOCODE_CACHE_TTL`` (30 days by default) — a neighbourhood is normally
    resolved once per deployment, not once per search. A *miss* is cached far
    less (``GEOCODE_NEGATIVE_CACHE_TTL``, 7 days): OpenStreetMap's coverage of
    Iran improves, and a stale "not found" must not outlive the fix.

    The cache key is built from the **normalised** query, so spelling variants
    share one entry.
    """
    key = cache_utils.make_key(
        "geocode",
        normalize_place_key(query),
        viewbox or "",
        "bounded" if bounded else "free",
    )

    cached = cache_utils.cache_get(key)
    if isinstance(cached, list):
        return cached

    # Re-check inside the lock: a waiter that finds the holder's result avoids
    # a second upstream call, which is the whole point of pacing.
    with cache_utils.with_lock(f"{key}:lock"):
        cached = cache_utils.cache_get(key)
        if isinstance(cached, list):
            return cached

        results = _fetch_upstream(query, viewbox, bounded)

        ttl = (
            getattr(settings, "GEOCODE_CACHE_TTL", 30 * 24 * 3600)
            if results
            else getattr(settings, "GEOCODE_NEGATIVE_CACHE_TTL", 7 * 24 * 3600)
        )
        cache_utils.cache_set(key, results, ttl)
        return results
