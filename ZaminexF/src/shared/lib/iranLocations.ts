// =============================================================================
//  Iran location coordinates + free geocoding helpers
// =============================================================================
// Leaflet needs a [lat, lng] to fly to when a province / city / district is
// selected. Provinces and the major cities have a static, always-available
// reference (no API, no key). Districts and any city not in the table are
// resolved on the fly through the app's own geocoding proxy with the
// province center as a reliable fallback — so the map always lands somewhere
// sensible even when offline.
// =============================================================================

export type LatLng = [number, number];

/** Approximate centre of each Iranian province, keyed by Persian display name. */
export const IRAN_PROVINCE_CENTERS: Record<string, LatLng> = {
  "آذربایجان شرقی": [38.0801, 46.2919],
  "آذربایجان غربی": [37.5527, 45.076],
  "اردبیل": [38.2498, 48.2933],
  "اصفهان": [32.6539, 51.666],
  "البرز": [35.8400, 50.9391],
  "ایلام": [33.6374, 46.4227],
  "بوشهر": [28.9234, 50.8203],
  "تهران": [35.6892, 51.389],
  "چهارمحال و بختیاری": [32.3256, 50.8644],
  "خراسان جنوبی": [32.8664, 59.2211],
  "خراسان رضوی": [36.2605, 59.6168],
  "خراسان شمالی": [37.475, 57.3333],
  "خوزستان": [31.3183, 48.6706],
  "زنجان": [36.6736, 48.4787],
  "سمنان": [35.583, 53.3867],
  "سیستان و بلوچستان": [29.4963, 60.8629],
  "فارس": [29.5918, 52.5837],
  "قزوین": [36.2681, 50.0041],
  "قم": [34.6401, 50.8764],
  "کردستان": [35.3144, 46.9988],
  "کرمان": [30.2839, 57.0834],
  "کرمانشاه": [34.3142, 47.065],
  "کهگیلویه و بویراحمد": [30.6684, 51.588],
  "گلستان": [36.8456, 54.4394],
  "گیلان": [37.2808, 49.5832],
  "لرستان": [33.4878, 48.3558],
  "مازندران": [36.5633, 53.0601],
  "مرکزی": [34.0916, 49.689],
  "هرمزگان": [27.1832, 56.2666],
  "همدان": [34.7983, 48.5147],
  "یزد": [31.8974, 54.3569],
};

/** Common Iranian cities → [lat, lng] (capital + a few large ones). */
export const IRAN_CITY_CENTERS: Record<string, LatLng> = {
  "تهران": [35.6892, 51.389],
  "مشهد": [36.2605, 59.6168],
  "اصفهان": [32.6539, 51.666],
  "کرج": [35.84, 50.9391],
  "شیراز": [29.5918, 52.5837],
  "تبریز": [38.0801, 46.2919],
  "اهواز": [31.3183, 48.6706],
  "قم": [34.6401, 50.8764],
  "کرمانشاه": [34.3142, 47.065],
  "ارومیه": [37.5527, 45.076],
  "رشت": [37.2808, 49.5832],
  "زاهدان": [29.4963, 60.8629],
  "همدان": [34.7983, 48.5147],
  "کرمان": [30.2839, 57.0834],
  "یزد": [31.8974, 54.3569],
  "اردبیل": [38.2498, 48.2933],
  "بندرعباس": [27.1832, 56.2666],
  "ساری": [36.5633, 53.0601],
  "اراک": [34.0916, 49.689],
  "سنندج": [35.3144, 46.9988],
  "زنجان": [36.6736, 48.4787],
  "گرگان": [36.8456, 54.4394],
  "خرم‌آباد": [33.4878, 48.3558],
  "قزوین": [36.2681, 50.0041],
  "سمنان": [35.583, 53.3867],
  "بجنورد": [37.475, 57.3333],
  "بیرجند": [32.8664, 59.2211],
  "شهرکرد": [32.3256, 50.8644],
  "یاسوج": [30.6684, 51.588],
  "بوشهر": [28.9234, 50.8203],
  "ایلام": [33.6374, 46.4227],
};

/**
 * Default view for maps without a located record: Mazandaran province.
 *
 * Centre is the midpoint of the province bounds (lat 35.9–36.9, lng 52.1–54.4):
 *   lat = (35.9 + 36.9) / 2 = 36.4,  lng = (52.1 + 54.4) / 2 = 53.25 ≈ 53.2.
 *
 * Zoom 8 is chosen for the smallest map surface (the picker panel is h-64 ≈
 * 256px): visible latitude span at zoom z ≈ 360 / 2^z, so z=8 → ≈1.4°, which
 * fits the province's ≈1.0° latitude span with margin; z=9 → ≈0.7° would crop it.
 */
export const DEFAULT_VIEW_CENTER: LatLng = [36.4, 53.2];
export const DEFAULT_VIEW_ZOOM = 8;

// ---------------------------------------------------------------------------
//  Persian place-name normalisation
// ---------------------------------------------------------------------------
//
// The tables above are keyed by display name, and the names actually selected
// in the form come from free-text reference data an administrator edits. The
// same city is routinely spelled several equivalent ways — "خرم‌آباد" with a
// ZWNJ, "خرم اباد" with a plain space, Arabic ي/ك instead of Persian ی/ک — and
// a plain object lookup treats those as different keys. The lookup then misses
// *silently*: no error, just a table that appears not to cover the city.
//
// Folding only genuine spelling variants (never letter order) makes the match
// robust without turning it into a fuzzy search. The server keeps an identical
// normaliser (apps/common/geocode.py) so a cache key and a table key agree.

// Letters that are *the same letter* for matching purposes: the Arabic forms of
// Persian ی/ک, and the alef family — "خرم‌آباد" is written with آ (ALEF WITH
// MADDA) or with a plain ا depending on who typed it, and both are that city.
const EQUIVALENT_LETTERS: Record<string, string> = {
  "\u064a": "\u06cc", // Arabic YEH    → Persian YEH
  "\u0643": "\u06a9", // Arabic KAF    → Persian KEHEH
  "\u0629": "\u0647", // TEH MARBUTA   → HEH
  "\u0649": "\u06cc", // ALEF MAKSURA  → Persian YEH
  "\u0622": "\u0627", // ALEF W/ MADDA → ALEF
  "\u0623": "\u0627", // ALEF W/ HAMZA ABOVE → ALEF
  "\u0625": "\u0627", // ALEF W/ HAMZA BELOW → ALEF
  "\u0671": "\u0627", // ALEF WASLA    → ALEF
};

/** ZWNJ / ZWJ / tatweel carry no matching value. */
const IGNORABLE_CHARACTERS = ["\u200c", "\u200d", "\u0640"];

export function normalizePlaceKey(value?: string | null): string {
  if (!value) return "";
  let text = value.normalize("NFC");
  for (const ch of IGNORABLE_CHARACTERS) text = text.split(ch).join("");
  text = Array.from(text)
    .filter((c) => !(c >= "\u064b" && c <= "\u0652")) // Arabic diacritics
    .map((c) => EQUIVALENT_LETTERS[c] ?? c)
    .join("");
  // Whitespace carries no identity here, so none of it survives — not even a
  // plain space. Removing the ZWNJ alone is not enough: the table holds
  // "خرم‌آباد" (ZWNJ) while operators type "خرم اباد" (space), and a key that
  // keeps one of the two would silently miss on the other.
  return text.split(/[\s\u200b-\u200f\u2060\ufeff]/).join("");
}

function normalizeCenters(table: Record<string, LatLng>): Record<string, LatLng> {
  const out: Record<string, LatLng> = {};
  for (const [name, center] of Object.entries(table)) {
    out[normalizePlaceKey(name)] = center;
  }
  return out;
}

const NORMALIZED_PROVINCE_CENTERS = normalizeCenters(IRAN_PROVINCE_CENTERS);
const NORMALIZED_CITY_CENTERS = normalizeCenters(IRAN_CITY_CENTERS);

/** Centre of a province/city from the static tables, or undefined. */
function centerFrom(table: Record<string, LatLng>, name: string): LatLng | undefined {
  const key = normalizePlaceKey(name);
  return key ? table[key] : undefined;
}

// ---------------------------------------------------------------------------
//  Geocoding — through the app's own proxy
// ---------------------------------------------------------------------------
//
// Requests go to ``/common/api/geocode/`` (apps/common/geocode.py) instead of
// to a public geocoder. Two reasons: the browser is then only ever talking to
// its own origin, which is what the Content-Security-Policy permits — a direct
// call was silently blocked and every search reported «نتیجه‌ای یافت نشد» — and
// the server is where the result gets cached, the upstream gets paced and a
// descriptive User-Agent gets sent.
//
// Pacing and the 429 retry that used to live here moved to the server, where a
// single budget covers every browser instead of one per tab.

/**
 * One lookup's outcome, with "the service is down" kept apart from "no match".
 *
 * Conflating the two is what made the original bug invisible: a blocked
 * request reported itself as a place that does not exist, so the operator kept
 * re-typing an address that was never looked up.
 */
type SearchOutcome =
  | { status: "found"; hit: GeocodeHit }
  | { status: "not_found" }
  | { status: "unavailable" };

async function geocodeSearch(
  query: string,
  viewbox: string | null,
  bounded: boolean
): Promise<SearchOutcome> {
  const params = new URLSearchParams();
  params.set("q", query);
  if (viewbox) {
    params.set("viewbox", viewbox);
    if (bounded) params.set("bounded", "1");
  }

  let res: Response;
  try {
    res = await fetch(`/common/api/geocode/?${params.toString()}`, {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
  } catch {
    // The request never completed — nothing was asked of the geocoder.
    return { status: "unavailable" };
  }
  // The proxy answers 503 when it could not reach its own upstream.
  if (!res.ok) return { status: "unavailable" };

  let data: unknown;
  try {
    data = await res.json();
  } catch {
    return { status: "unavailable" };
  }
  if (!Array.isArray(data)) return { status: "unavailable" };

  const row = data[0] as
    | { lat?: unknown; lon?: unknown; address?: Record<string, string> }
    | undefined;
  const lat = Number(row?.lat);
  const lon = Number(row?.lon);
  if (!row || !Number.isFinite(lat) || !Number.isFinite(lon)) {
    return { status: "not_found" };
  }
  return { status: "found", hit: { lat, lon, address: row.address || undefined } };
}

/** Coarse bounding box around a known province centre, as the geocoder's
 * `viewbox` parameter (west,north,east,south). Widened (halfLat 2.2 /
 * halfLng 3.5) so places near a province edge still rank correctly. */
function provinceViewbox(provinceName?: string): string | null {
  const c = provinceName ? centerFrom(NORMALIZED_PROVINCE_CENTERS, provinceName) : null;
  if (!c) return null;
  const [lat, lng] = c;
  const halfLat = 2.2;
  const halfLng = 3.5;
  return [
    (lng - halfLng).toFixed(2),
    (lat + halfLat).toFixed(2),
    (lng + halfLng).toFixed(2),
    (lat - halfLat).toFixed(2),
  ].join(",");
}

/** A geocoder hit, with its `address` kept for the acceptance rule. */
export type GeocodeHit = {
  lat: number;
  lon: number;
  address?: Record<string, string>;
};

/**
 * Ordered query variants for a structured selection (most specific first).
 * A repeated name (a محله that exists in many cities) ranks its selected
 * parents first; the bare name is the last resort.
 */
export function buildQueryVariants(
  name: string,
  kind: "city" | "district",
  context?: { provinceName?: string; cityName?: string }
): string[] {
  const clean = (name || "").trim();
  const province = context?.provinceName?.trim();
  const city = context?.cityName?.trim();
  if (!clean) return [];
  const variants: string[] = [];
  if (kind === "district") {
    if (city && province) variants.push(`${clean}, ${city}, ${province}`);
    if (province) variants.push(`${clean}, ${province}`);
  } else if (province) {
    variants.push(`${clean}, ${province}`);
  }
  variants.push(clean);
  return variants;
}

/** A variant is "fully qualified" when it embeds every selected parent, so
 * the geocoder's own ranking (not `bounded=1`) keeps the result in-province. */
export function variantIsFullyQualified(
  variant: string,
  kind: "city" | "district",
  context?: { provinceName?: string; cityName?: string }
): boolean {
  const province = context?.provinceName?.trim();
  const city = context?.cityName?.trim();
  if (kind === "district") {
    return (!city || variant.includes(city)) && (!province || variant.includes(province));
  }
  return !province || variant.includes(province);
}

/**
 * Acceptance rule for a structured-selection hit (keep it simple):
 *  - fully qualified query → accept the top hit (ranking already correct);
 *  - partially qualified → hard-reject only a *clear* mismatch: the hit's
 *    jsonv2 `address` names a different known province (Persian). Missing
 *    address info and English-only labels (which we cannot transliterate) are
 *    treated as "no clear mismatch" → accept.
 */
export function acceptsResult(
  hit: GeocodeHit,
  province: string | undefined,
  fullyQualified: boolean
): boolean {
  if (fullyQualified) return true;
  if (!province) return true;
  const values = Object.values(hit.address || {})
    .map((v) => normalizePlaceKey(String(v)))
    .filter(Boolean);
  if (values.length === 0) return true;
  const wanted = normalizePlaceKey(province);
  for (const value of values) {
    for (const other of Object.keys(NORMALIZED_PROVINCE_CENTERS)) {
      if (other !== wanted && value === other) return false;
    }
  }
  return true;
}

/** Result of a resolution, so a caller can tell *why* nothing came back. */
export type ResolveOutcome =
  | { status: "found"; location: LatLng }
  | { status: "not_found" }
  | { status: "unavailable" };

const NOT_FOUND: ResolveOutcome = { status: "not_found" };
const UNAVAILABLE: ResolveOutcome = { status: "unavailable" };

/** Bounded first (keep the hit inside the province), then unbounded. */
/** Which pass produced a hit matters: only the bounded one is in-province. */
type SearchAttempt =
  | { status: "unavailable" }
  | { status: "not_found" }
  | { status: "found"; hit: GeocodeHit; bounded: boolean };

async function searchWithFallback(
  query: string,
  viewbox: string | null
): Promise<SearchAttempt> {
  if (!viewbox) {
    const attempt = await geocodeSearch(query, null, false);
    return attempt.status === "found" ? { ...attempt, bounded: false } : attempt;
  }
  const bounded = await geocodeSearch(query, viewbox, true);
  // A downed geocoder is down for the retry too — fail fast instead of making
  // the operator wait through a second timeout.
  if (bounded.status !== "not_found") {
    return bounded.status === "found" ? { ...bounded, bounded: true } : bounded;
  }
  const unbounded = await geocodeSearch(query, viewbox, false);
  return unbounded.status === "found" ? { ...unbounded, bounded: false } : unbounded;
}

/**
 * Resolve a place name, reporting *why* it failed.
 *
 *  - A **province** or a **city** in the static tables resolves offline and
 *    instantly; only the rest reach the geocoder. ``IRAN_CITY_CENTERS`` used
 *    to sit unused, so every city — including the 31 largest — paid a network
 *    round trip and failed outright with no internet.
 *  - A **city** / **district** structured selection (``options.variants``)
 *    walks an ordered ladder of queries (most specific first) so a repeated
 *    name resolves to its selected parents, and applies :func:`acceptsResult`.
 *  - **Free-text search** (the picker's search box) keeps its single qualified
 *    query with the bounded→unbounded fallback, and now applies
 *    :func:`acceptsResult` too — it used to accept the top hit unconditionally,
 *    so a homonymous neighbourhood in another province could be returned.
 */
export async function resolvePlace(
  name: string,
  kind: "province" | "city" | "district",
  context?: { provinceName?: string; cityName?: string },
  options?: { variants?: boolean }
): Promise<ResolveOutcome> {
  const clean = (name || "").trim();
  if (!clean) return NOT_FOUND;

  const province = context?.provinceName?.trim() || undefined;
  const city = context?.cityName?.trim() || undefined;
  const viewbox = provinceViewbox(province);

  // Static tables first: no network, no latency, works offline.
  if (kind === "province") {
    const center = centerFrom(NORMALIZED_PROVINCE_CENTERS, clean);
    if (center) return { status: "found", location: center };
    const outcome = await geocodeSearch(clean, null, false);
    if (outcome.status === "unavailable") return UNAVAILABLE;
    return outcome.status === "found"
      ? { status: "found", location: [outcome.hit.lat, outcome.hit.lon] }
      : NOT_FOUND;
  }
  if (kind === "city") {
    const center = centerFrom(NORMALIZED_CITY_CENTERS, clean);
    if (center) return { status: "found", location: center };
  }

  if (!options?.variants) {
    // Free-text search: one qualified query, bounded → unbounded.
    const parts = [clean];
    if (kind === "district" && city) parts.push(city);
    if (province) parts.push(province);
    const q = parts.join(", ");
    const qualified = variantIsFullyQualified(q, kind, context);
    const attempt = await searchWithFallback(q, viewbox);
    if (attempt.status === "unavailable") return UNAVAILABLE;
    if (attempt.status !== "found") return NOT_FOUND;
    // A hit from the bounded pass is inside the province viewbox by
    // construction, so a fully qualified query may trust the ranker there. The
    // unbounded retry deliberately drops that constraint — it runs precisely
    // because nothing matched in-province — so its hit always gets checked.
    // Without this the search box answered a homonymous neighbourhood in
    // another province, which is how «گلستان, مازندران» used to land in Tehran.
    return acceptsResult(attempt.hit, province, qualified && attempt.bounded)
      ? { status: "found", location: [attempt.hit.lat, attempt.hit.lon] }
      : NOT_FOUND;
  }

  // Structured selection: ordered variant ladder + acceptance rule.
  for (const variant of buildQueryVariants(clean, kind, context)) {
    const fullyQualified = variantIsFullyQualified(variant, kind, context);
    const outcome = await geocodeSearch(variant, viewbox, !fullyQualified);
    if (outcome.status === "unavailable") return UNAVAILABLE;
    if (outcome.status === "found" && acceptsResult(outcome.hit, province, fullyQualified)) {
      return { status: "found", location: [outcome.hit.lat, outcome.hit.lon] };
    }
  }
  return NOT_FOUND;
}

/**
 * Coordinates only — ``null`` when the place could not be resolved.
 *
 * Kept for callers that only move a camera and do not need to know why nothing
 * came back; :func:`resolvePlace` is the one that distinguishes a miss from an
 * unavailable geocoder.
 */
export async function resolvePlaceCoordinates(
  name: string,
  kind: "province" | "city" | "district",
  context?: { provinceName?: string; cityName?: string },
  options?: { variants?: boolean }
): Promise<LatLng | null> {
  const outcome = await resolvePlace(name, kind, context, options);
  return outcome.status === "found" ? outcome.location : null;
}
