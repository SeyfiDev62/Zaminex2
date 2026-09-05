import { afterEach, describe, expect, it, vi } from "vitest";
import {
  acceptsResult,
  buildQueryVariants,
  normalizePlaceKey,
  resolvePlace,
  resolvePlaceCoordinates,
  variantIsFullyQualified,
  type GeocodeHit,
} from "./iranLocations";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function qOf(url: string): string | null {
  // Requests are same-origin and therefore relative; a base is needed to parse.
  return new URL(url, "http://test.local").searchParams.get("q");
}

function paramsOf(url: string): URLSearchParams {
  return new URL(String(url), "http://test.local").searchParams;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// ─────────────────────────────────────────────────────────────────────────────
// Normaliser — shared contract with the Python side
// ─────────────────────────────────────────────────────────────────────────────
//
// These exact cases are asserted in
// ZaminexB/apps/common/tests/test_geocode.py (PARITY_CASES) too. The server
// builds its cache key with its own copy of this function; if the two ever
// drift, a lookup stops matching its cached entry and fails silently. Keeping
// one table in both suites turns that drift into a failing test.

const PARITY_CASES: Array<[string, string]> = [
  ["خرم‌آباد", "خرماباد"],
  ["خرم اباد", "خرماباد"],
  ["بندر  عباس", "بندرعباس"],
  ["آبادان", "ابادان"],
  ["قائم‌شهر", "قائمشهر"],
  ["قائم شهر", "قائمشهر"],
  ["مشهد", "مشهد"],
  ["", ""],
  ["   ", ""],
];

describe("normalizePlaceKey", () => {
  it("matches the Python implementation's expectations", () => {
    for (const [raw, expected] of PARITY_CASES) {
      expect(normalizePlaceKey(raw)).toBe(expected);
    }
  });

  it("never merges two genuinely different names", () => {
    expect(normalizePlaceKey("تهران")).not.toBe(normalizePlaceKey("تهرانر"));
    expect(normalizePlaceKey("کرج")).not.toBe(normalizePlaceKey("گرگ"));
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Pure logic: variant ladder
// ─────────────────────────────────────────────────────────────────────────────

describe("buildQueryVariants", () => {
  it("district with city + province → most specific first", () => {
    expect(
      buildQueryVariants("گلستان", "district", {
        provinceName: "مازندران",
        cityName: "ساری",
      })
    ).toEqual(["گلستان, ساری, مازندران", "گلستان, مازندران", "گلستان"]);
  });

  it("district with province only → skip the city slot", () => {
    expect(
      buildQueryVariants("گلستان", "district", { provinceName: "مازندران" })
    ).toEqual(["گلستان, مازندران", "گلستان"]);
  });

  it("district with no parents → bare name only", () => {
    expect(buildQueryVariants("گلستان", "district")).toEqual(["گلستان"]);
  });

  it("city with province → qualified then bare", () => {
    expect(
      buildQueryVariants("ساری", "city", { provinceName: "مازندران" })
    ).toEqual(["ساری, مازندران", "ساری"]);
  });

  it("city with no province → bare name only", () => {
    expect(buildQueryVariants("ساری", "city")).toEqual(["ساری"]);
  });

  it("empty name → no variants", () => {
    expect(buildQueryVariants("  ", "city")).toEqual([]);
  });
});

describe("variantIsFullyQualified", () => {
  it("district with all parents present", () => {
    expect(
      variantIsFullyQualified("گلستان, ساری, مازندران", "district", {
        provinceName: "مازندران",
        cityName: "ساری",
      })
    ).toBe(true);
  });

  it("district missing the city is not fully qualified", () => {
    expect(
      variantIsFullyQualified("گلستان, مازندران", "district", {
        provinceName: "مازندران",
        cityName: "ساری",
      })
    ).toBe(false);
  });

  it("bare district name is not fully qualified", () => {
    expect(
      variantIsFullyQualified("گلستان", "district", {
        provinceName: "مازندران",
        cityName: "ساری",
      })
    ).toBe(false);
  });

  it("city with province is fully qualified", () => {
    expect(
      variantIsFullyQualified("ساری, مازندران", "city", { provinceName: "مازندران" })
    ).toBe(true);
  });

  it("bare city with a selected province is not fully qualified", () => {
    expect(
      variantIsFullyQualified("ساری", "city", { provinceName: "مازندران" })
    ).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Pure logic: acceptance rule
// ─────────────────────────────────────────────────────────────────────────────

describe("acceptsResult", () => {
  const hit = (address?: Record<string, string>): GeocodeHit => ({
    lat: 36.5,
    lon: 53.0,
    address,
  });

  it("fully qualified → accept top hit regardless of address", () => {
    expect(acceptsResult(hit({ province: "تهران" }), "مازندران", true)).toBe(true);
  });

  it("no province context → accept", () => {
    expect(acceptsResult(hit({ province: "تهران" }), undefined, false)).toBe(true);
  });

  it("missing address info → accept", () => {
    expect(acceptsResult(hit(undefined), "مازندران", false)).toBe(true);
  });

  it("English-only address (untranslatable) → accept (no clear mismatch)", () => {
    expect(
      acceptsResult(hit({ province: "Mazandaran Province", city: "Sari" }), "مازندران", false)
    ).toBe(true);
  });

  it("matching Persian province → accept", () => {
    expect(acceptsResult(hit({ province: "مازندران" }), "مازندران", false)).toBe(true);
  });

  it("a different Persian province → hard reject", () => {
    expect(acceptsResult(hit({ province: "تهران" }), "مازندران", false)).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Async orchestration (fetch mocked; the request goes to the same-origin proxy)
// ─────────────────────────────────────────────────────────────────────────────

describe("resolvePlace / resolvePlaceCoordinates", () => {
  it("province → static table, no network", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const result = await resolvePlaceCoordinates("مازندران", "province");
    expect(result).toEqual([36.5633, 53.0601]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("structured city → qualified variant first, accepts top hit", async () => {
    // نوشهر is deliberately absent from IRAN_CITY_CENTERS: this exercises the
    // network path rather than the static-table short-circuit.
    const fetchMock = vi.fn(async () =>
      jsonResponse([
        { lat: "36.6511", lon: "51.4965", address: { city: "Nowshahr", province: "Mazandaran Province" } },
      ])
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates(
      "نوشهر",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(result).toEqual([36.6511, 51.4965]);
    const queries = fetchMock.mock.calls.map(([u]) => qOf(String(u)));
    expect(queries).toEqual(["نوشهر, مازندران"]);
  });

  it("city in the static table → resolved offline, zero network calls", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    // ساری is one of the 31 provincial capitals kept in the static table.
    const result = await resolvePlaceCoordinates(
      "ساری",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(result).toEqual([36.5633, 53.0601]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("table lookup matches a differently spelled name (ZWNJ / Arabic letters)", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    // "خرم اباد" with a plain space + Arabic ي instead of the table's
    // "خرم‌آباد" (ZWNJ + Persian ی) — same city, different spelling.
    expect(normalizePlaceKey("خرم اباد")).toBe(normalizePlaceKey("خرم‌آباد"));
    const result = await resolvePlaceCoordinates("خرم اباد", "city", undefined, {
      variants: true,
    });
    expect(result).toEqual([33.4878, 48.3558]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requests go to the same-origin proxy with q / viewbox / bounded", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse([{ lat: "36.6511", lon: "51.4965", address: { city: "Nowshahr" } }])
    );
    vi.stubGlobal("fetch", fetchMock);

    await resolvePlaceCoordinates("نوشهر", "city", { provinceName: "مازندران" }, { variants: true });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url).startsWith("/common/api/geocode/?")).toBe(true);
    const params = paramsOf(url);
    expect(params.get("q")).toBe("نوشهر, مازندران");
    expect(params.get("viewbox")).toMatch(/^[-\d.]+,[-\d.]+,[-\d.]+,[-\d.]+$/);
    // The query already names the province, so it is fully qualified and the
    // ranker's own ordering is trusted — `bounded` is deliberately omitted.
    expect(params.has("bounded")).toBe(false);
  });

  it("only a not-yet-fully-qualified variant is sent bounded", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await resolvePlaceCoordinates(
      "مرکزی",
      "district",
      { provinceName: "مازندران", cityName: "ساری" },
      { variants: true }
    );

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [first, second, third] = fetchMock.mock.calls.map(([u]) => paramsOf(String(u)));
    expect(first.get("q")).toBe("مرکزی, ساری, مازندران");
    expect(first.has("bounded")).toBe(false);
    expect(second.get("q")).toBe("مرکزی, مازندران");
    // The later variants drop the city and then the province, so they no
    // longer pin everything that is known — the viewbox has to do the
    // constraining instead.
    expect(second.get("bounded")).toBe("1");
    expect(third.get("q")).toBe("مرکزی");
    expect(third.get("bounded")).toBe("1");
  });

  it("structured district → ladder falls through to the next variant", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const q = qOf(String(url));
      if (q === "مرکزی, ساری, مازندران") return jsonResponse([]); // not found
      if (q === "مرکزی, مازندران")
        return jsonResponse([
          { lat: "36.56", lon: "53.06", address: { city: "Sari", province: "Mazandaran Province" } },
        ]);
      throw new Error(`unexpected query: ${q}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates(
      "مرکزی",
      "district",
      { provinceName: "مازندران", cityName: "ساری" },
      { variants: true }
    );

    expect(result).toEqual([36.56, 53.06]);
    const queries = fetchMock.mock.calls.map(([u]) => qOf(String(u)));
    expect(queries).toEqual(["مرکزی, ساری, مازندران", "مرکزی, مازندران"]);
  });

  it("partially-qualified hit in the wrong province is rejected → null", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const q = qOf(String(url));
      if (q === "نوشهر, مازندران") return jsonResponse([]);
      if (q === "نوشهر")
        return jsonResponse([{ lat: "35.6892", lon: "51.389", address: { province: "تهران" } }]);
      throw new Error(`unexpected query: ${q}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates(
      "نوشهر",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(result).toBeNull();
    const queries = fetchMock.mock.calls.map(([u]) => qOf(String(u)));
    expect(queries).toEqual(["نوشهر, مازندران", "نوشهر"]);
  });

  it("offline (fetch rejects) → unavailable, and the wrapper still yields null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      })
    );
    const outcome = await resolvePlace(
      "نوشهر",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );
    expect(outcome.status).toBe("unavailable");
    const result = await resolvePlaceCoordinates(
      "نوشهر",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );
    expect(result).toBeNull();
  });

  it("proxy 503 (upstream unreachable) → unavailable, ladder stops at once", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ detail: "سرویس در دسترس نیست" }, 503));
    vi.stubGlobal("fetch", fetchMock);

    const outcome = await resolvePlace(
      "نوشهر",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(outcome).toEqual({ status: "unavailable" });
    // A downed geocoder is down for the remaining variants too — retrying the
    // ladder would only make the operator wait out one timeout per variant.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("empty result from the proxy → not_found, distinct from unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));
    const outcome = await resolvePlace("نوشهر", "city", { provinceName: "مازندران" }, {
      variants: true,
    });
    expect(outcome).toEqual({ status: "not_found" });
  });

  it("free-text search (no variants) → single qualified query", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse([{ lat: "36.56", lon: "53.06", address: { city: "Sari" } }])
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates("گلستان", "district", {
      provinceName: "مازندران",
      cityName: "ساری",
    });

    expect(result).toEqual([36.56, 53.06]);
    const queries = fetchMock.mock.calls.map(([u]) => qOf(String(u)));
    // Single query, qualified with city + province (today's behaviour).
    expect(queries).toEqual(["گلستان, ساری, مازندران"]);
  });

  it("free-text search now rejects a hit in the wrong province", async () => {
    // The acceptance rule used to guard only the structured path, so the search
    // box could happily return a homonymous neighbourhood elsewhere.
    // گلستان is a real district in Tehran as well: the bounded pass misses
    // and the unbounded retry hands back the Tehran one.
    const fetchMock = vi.fn(async (url: string) => {
      if (paramsOf(url).get("bounded") === "1") return jsonResponse([]);
      return jsonResponse([{ lat: "35.6892", lon: "51.389", address: { province: "تهران" } }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates("گلستان", "district", {
      provinceName: "مازندران",
    });

    expect(result).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("free-text search retries without bounded when the bounded pass misses", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const params = paramsOf(url);
      if (params.get("bounded") === "1") return jsonResponse([]);
      return jsonResponse([{ lat: "36.56", lon: "53.06", address: { city: "Sari" } }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates("گلستان", "district", {
      provinceName: "مازندران",
    });

    expect(result).toEqual([36.56, 53.06]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(paramsOf(fetchMock.mock.calls[0][0]).get("bounded")).toBe("1");
    expect(paramsOf(fetchMock.mock.calls[1][0]).has("bounded")).toBe(false);
  });

  it("empty name → not_found, no request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const outcome = await resolvePlace("   ", "district");
    expect(outcome).toEqual({ status: "not_found" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
