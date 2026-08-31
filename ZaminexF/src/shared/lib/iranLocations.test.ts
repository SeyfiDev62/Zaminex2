import { afterEach, describe, expect, it, vi } from "vitest";
import {
  acceptsResult,
  buildQueryVariants,
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
  return new URL(url).searchParams.get("q");
}

afterEach(() => {
  vi.unstubAllGlobals();
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
// Async orchestration (fetch mocked; real timers so the 1.1s queue is honoured)
// ─────────────────────────────────────────────────────────────────────────────

describe("resolvePlaceCoordinates", () => {
  it("province → static table, no network", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const result = await resolvePlaceCoordinates("مازندران", "province");
    expect(result).toEqual([36.5633, 53.0601]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("structured city → qualified variant first, accepts top hit", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse([
        { lat: "36.5633", lon: "53.0601", address: { city: "Sari", province: "Mazandaran Province" } },
      ])
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates(
      "ساری",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(result).toEqual([36.5633, 53.0601]);
    const queries = fetchMock.mock.calls.map(([u]) => qOf(String(u)));
    expect(queries).toEqual(["ساری, مازندران"]);
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
      if (q === "ساری, مازندران") return jsonResponse([]);
      if (q === "ساری")
        return jsonResponse([{ lat: "35.6892", lon: "51.389", address: { province: "تهران" } }]);
      throw new Error(`unexpected query: ${q}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates(
      "ساری",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(result).toBeNull();
    const queries = fetchMock.mock.calls.map(([u]) => qOf(String(u)));
    expect(queries).toEqual(["ساری, مازندران", "ساری"]);
  });

  it("offline (fetch rejects) → null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      })
    );
    const result = await resolvePlaceCoordinates(
      "ساری",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );
    expect(result).toBeNull();
  });

  it("429 is retried once, then succeeds", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async () => {
      calls += 1;
      if (calls === 1) return jsonResponse({}, 429);
      return jsonResponse([{ lat: "36.5633", lon: "53.0601", address: { city: "Sari" } }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolvePlaceCoordinates(
      "ساری",
      "city",
      { provinceName: "مازندران" },
      { variants: true }
    );

    expect(result).toEqual([36.5633, 53.0601]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
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
});
