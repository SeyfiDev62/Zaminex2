// =============================================================================
//  Iran location coordinates + free geocoding helpers
// =============================================================================
// Leaflet needs a [lat, lng] to fly to when a province / city / district is
// selected. Provinces and the major cities have a static, always-available
// reference (no API, no key). Districts and any city not in the table are
// resolved on the fly via Nominatim (OpenStreetMap's free geocoder) with the
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
//  Nominatim (OSM) geocoding — free, no key
// ---------------------------------------------------------------------------

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Nominatim's usage policy allows ~1 request/second. The map picker can fire
// several resolutions in a row (province → city → district), so every request
// waits its turn and one 429 is retried once instead of failing the zoom.
let lastNominatimAt = 0;

async function nominatimFetch(url: string): Promise<Response> {
  const wait = lastNominatimAt + 1100 - Date.now();
  if (wait > 0) await sleep(wait);
  lastNominatimAt = Date.now();
  let res = await fetch(url, { headers: { Accept: "application/json" } });
  if (res.status === 429) {
    await sleep(1600);
    lastNominatimAt = Date.now();
    res = await fetch(url, { headers: { Accept: "application/json" } });
  }
  return res;
}

/** Coarse bounding box around a known province centre, as Nominatim's
 * `viewbox` parameter (west,north,east,south). Widened (halfLat 2.2 /
 * halfLng 3.5) so places near a province edge still rank correctly. */
function provinceViewbox(provinceName?: string): string | null {
  const c = provinceName ? IRAN_PROVINCE_CENTERS[provinceName.trim()] : null;
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

/** A top hit from Nominatim, with its `address` kept for the acceptance rule. */
export type GeocodeHit = {
  lat: number;
  lon: number;
  address?: Record<string, string>;
};

/** One queued Nominatim request. `bounded` adds `bounded=1` (keep the result
 * inside the viewbox). Returns the top hit or null (offline / not found). */
async function nominatimSearch(
  query: string,
  viewbox: string | null,
  bounded: boolean
): Promise<GeocodeHit | null> {
  try {
    const url = new URL("https://nominatim.openstreetmap.org/search");
    url.searchParams.set("q", query);
    url.searchParams.set("countrycodes", "ir");
    url.searchParams.set("format", "jsonv2");
    url.searchParams.set("limit", "1");
    if (viewbox) {
      url.searchParams.set("viewbox", viewbox);
      if (bounded) url.searchParams.set("bounded", "1");
    }
    const res = await nominatimFetch(url.toString());
    if (!res.ok) return null;
    const data = await res.json();
    if (Array.isArray(data) && data.length > 0) {
      const lat = parseFloat(data[0].lat);
      const lon = parseFloat(data[0].lon);
      if (!Number.isNaN(lat) && !Number.isNaN(lon)) {
        return { lat, lon, address: data[0].address || undefined };
      }
    }
  } catch {
    // offline / blocked → null (caller applies the NO-MOVE fallback)
  }
  return null;
}

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
 * Nominatim's own ranking (not `bounded=1`) keeps the result in-province. */
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
  const values = Object.values(hit.address || {}).map((v) => String(v).trim());
  if (values.length === 0) return true;
  for (const value of values) {
    for (const other of Object.keys(IRAN_PROVINCE_CENTERS)) {
      if (other !== province && value === other) return false;
    }
  }
  return true;
}

/**
 * Resolve a place name to coordinates.
 *
 *  - A **province** uses the static centre table (already exact, no internet);
 *    the geocoder only covers provinces not in the table.
 *  - A **city** / **district** structured selection (`options.variants = true`)
 *    tries an ordered ladder of queries (most specific first) so a repeated
 *    name resolves to its selected parents, and applies the acceptance rule
 *    above. On failure it returns null — the caller must NOT move the camera.
 *  - Free-text search (the picker's search box, `options.variants` omitted)
 *    keeps the single qualified query with the bounded→unbounded fallback and
 *    no acceptance rule (today's behaviour, unchanged).
 *
 * Returns null when nothing can be resolved.
 */
export async function resolvePlaceCoordinates(
  name: string,
  kind: "province" | "city" | "district",
  context?: { provinceName?: string; cityName?: string },
  options?: { variants?: boolean }
): Promise<LatLng | null> {
  const clean = (name || "").trim();
  if (!clean) return null;

  const province = context?.provinceName?.trim() || undefined;
  const city = context?.cityName?.trim() || undefined;
  const viewbox = provinceViewbox(province);

  if (kind === "province") {
    const c = IRAN_PROVINCE_CENTERS[clean];
    if (c) return c;
    const hit = await nominatimSearch(clean, null, false);
    return hit ? [hit.lat, hit.lon] : null;
  }

  if (!options?.variants) {
    // Free-text search: today's single qualified query, bounded → unbounded,
    // top hit accepted as-is.
    const parts = [clean];
    if (kind === "district" && city) parts.push(city);
    if (province) parts.push(province);
    const q = parts.join(", ");
    const hit = viewbox
      ? (await nominatimSearch(q, viewbox, true)) ??
        (await nominatimSearch(q, viewbox, false))
      : await nominatimSearch(q, null, false);
    return hit ? [hit.lat, hit.lon] : null;
  }

  // Structured selection: ordered variant ladder + acceptance rule.
  for (const variant of buildQueryVariants(clean, kind, context)) {
    const fullyQualified = variantIsFullyQualified(variant, kind, context);
    const hit = await nominatimSearch(variant, viewbox, !fullyQualified);
    if (hit && acceptsResult(hit, province, fullyQualified)) {
      return [hit.lat, hit.lon];
    }
  }
  return null;
}
