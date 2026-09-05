import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Search, Crosshair, MapPin, Check, Loader2 } from "lucide-react";
import {
  DEFAULT_VIEW_CENTER,
  DEFAULT_VIEW_ZOOM,
  resolvePlace,
  type LatLng,
} from "../../lib/iranLocations";
import { apiFetch } from "../../lib/apiClient";
import { consultantMarkerColor } from "../../lib/consultantColors";
import {
  makePinIcon,
  PropertyMarkerPopupBody,
} from "./PropertyMarkerPopup";

// The fixed selection marker: the SAME teardrop pin as the located-property
// markers (identical template/size/anchor via makePinIcon) in the fixed
// primary green, so its tip lands exactly on the map centre and stays there
// while the user drags the map around it.
const centerMarkerIcon = makePinIcon("#0BB68A");

function roundCoord(n: number): number {
  // Backend DecimalField(max_digits=9, decimal_places=6) rejects raw Leaflet
  // floats (13+ digits) with a confusing "no more than 9 digits" error.
  return Number(n.toFixed(6));
}

function sameCoord(a: number, b: number): boolean {
  return Math.abs(roundCoord(a) - roundCoord(b)) < 1e-9;
}

/**
 * The two ways a place lookup can come back empty, in the operator's words.
 *
 * Kept apart on purpose: a miss is an answer («there is no such place»), while
 * an unavailable geocoder is the absence of one — the operator should not go
 * hunting for a typo that was never checked.
 */
const NOTICE_TEXT = {
  unavailable: "جستجوی مکان در دسترس نیست؛ اتصال شبکه یا سرویس نقشه را بررسی کنید.",
  not_found: "نتیجه‌ای یافت نشد",
} as const;

type LocatedProperty = {
  id: number;
  title: string;
  lat: number;
  lng: number;
  status: string;
  area: number;
  consultantId: string | number | null;
  consultantName: string;
};

/** Leaflet tiles and click coords drift inside RTL documents until the size is known. */
function FitMapSize() {
  const map = useMap();
  useEffect(() => {
    const id = window.setTimeout(() => map.invalidateSize(), 40);
    return () => window.clearTimeout(id);
  }, [map]);
  return null;
}

/** Reports the live map centre (the fixed marker position) on every move. */
function CenterTracker({ onCenter }: { onCenter: (c: LatLng) => void }) {
  const map = useMap();
  const lastRef = useRef<LatLng | null>(null);
  const report = useCallback(() => {
    const c = map.getCenter();
    const next: LatLng = [c.lat, c.lng];
    const last = lastRef.current;
    if (!last || Math.abs(last[0] - next[0]) > 1e-9 || Math.abs(last[1] - next[1]) > 1e-9) {
      lastRef.current = next;
      onCenter(next);
    }
  }, [map, onCenter]);
  useMapEvents({ move: report, zoomend: report });
  useEffect(() => {
    report();
  }, [report]);
  return null;
}

/** Flying the camera to a target (search results, district change, coord entry). */
function FlyToLocation({
  location,
  zoom,
}: {
  location: LatLng | null;
  zoom: number;
}) {
  const map = useMap();
  useEffect(() => {
    if (location) map.flyTo(location, zoom, { duration: 0.8 });
  }, [map, location, zoom]);
  return null;
}

function PropertyMapPicker({
  value,
  onChange,
  provinceName,
  cityName,
  districtName,
  csrfToken,
}: {
  /** The confirmed (registered) coordinates. */
  value: LatLng | null;
  /** Called with the confirmed centre when the user taps «تایید موقعیت ملک». */
  onChange: (p: LatLng) => void;
  provinceName?: string;
  cityName?: string;
  districtName?: string;
  csrfToken?: string;
}) {
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [focusTarget, setFocusTarget] = useState<{ location: LatLng; zoom: number } | null>(null);
  // A short auto-dismissing notice over the map. Held as text rather than a
  // boolean because "nothing matched" and "the geocoder is down" need to be
  // told apart — the first is an answer, the second is not an answer at all.
  const [mapNotice, setMapNotice] = useState("");
  const noResultTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // On edit the marker is already saved. Do not fly to the district centre on
  // first hydrate — that used to yank the camera off the real marker.
  const skipInitialFly = useRef(Boolean(value));
  const didHydrateLocation = useRef(false);

  // Live map centre — the position of the fixed marker. The coordinates
  // rendered under the map and the confirm-button state both follow this.
  const [center, setCenter] = useState<LatLng>(value ?? DEFAULT_VIEW_CENTER);
  const centerRef = useRef(center);
  centerRef.current = center;
  const onCenter = useCallback((c: LatLng) => setCenter(c), []);

  // All located properties, drawn as consultant-coloured markers so the
  // user can see the surroundings (and avoid registering on top of another
  // property — the backend rejects exact duplicates as well).
  // Read from the compact `options` projection in one request (same pattern
  // as the combobox fetch in App.tsx) — it carries exactly the columns the
  // markers below use, so there is no need to page the full list endpoint.
  const [located, setLocated] = useState<LocatedProperty[]>([]);
  useEffect(() => {
    if (!csrfToken) return;
    let cancelled = false;
    (async () => {
      let rows: any[] = [];
      try {
        const res = await apiFetch(
          "/properties/api/properties/options/?scope=all",
          { method: "GET" },
          csrfToken
        );
        if (res.ok) rows = await res.json();
      } catch {
        // surrounding properties are context only — never block the form.
      }
      if (cancelled) return;
      setLocated(
        rows
          .filter((r) => r.latitude != null && r.longitude != null)
          .map((r) => ({
            id: r.id,
            title: r.title || "ملک",
            lat: Number(r.latitude),
            lng: Number(r.longitude),
            status: String(r.propertyStatus || "").toUpperCase(),
            area: Number(r.area || 0),
            consultantId: r.consultantId ?? null,
            consultantName: r.consultantName || "نامشخص",
          }))
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [csrfToken]);

  // Clear the pending "no result" timer on unmount so we never update state
  // on an unmounted component.
  useEffect(() => {
    return () => {
      if (noResultTimer.current) clearTimeout(noResultTimer.current);
    };
  }, []);

  const showNotice = useCallback((message: string) => {
    if (noResultTimer.current) clearTimeout(noResultTimer.current);
    setMapNotice(message);
    noResultTimer.current = setTimeout(() => setMapNotice(""), 7000);
  }, []);

  // When a province / city / district changes, resolve and fly there.
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      const name = districtName || cityName || provinceName || "";

      if (!didHydrateLocation.current) {
        // Edit form: district labels arrive after the location tree loads.
        if (!name && skipInitialFly.current) return;
        didHydrateLocation.current = true;
        if (skipInitialFly.current) return;
      }

      if (!name) {
        if (!value) setFocusTarget({ location: DEFAULT_VIEW_CENTER, zoom: DEFAULT_VIEW_ZOOM });
        return;
      }
      const kind = districtName ? "district" : cityName ? "city" : "province";
      const outcome = await resolvePlace(
        name,
        kind,
        { provinceName, cityName },
        { variants: true }
      );
      if (cancelled) return;
      if (outcome.status === "found") {
        const zoom = districtName ? 15 : cityName ? 12 : 8;
        setFocusTarget({ location: outcome.location, zoom });
      } else if (kind !== "province") {
        // NO-MOVE fallback: a city or district that could not be resolved must
        // not drag the camera somewhere arbitrary. Provinces and the 31 cities
        // in the static table always resolve offline, so in practice this is a
        // district (or an unlisted city) with the geocoder out of reach.
        showNotice(NOTICE_TEXT[outcome.status]);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [provinceName, cityName, districtName]); // eslint-disable-line react-hooks/exhaustive-deps

  // A coordinate entered in the form fields (or any external change of the
  // confirmed value) moves the camera there so the fixed marker lands on it.
  // A confirm coming from the map itself is skipped — the camera is already
  // on that centre and re-flying would be a pointless hop.
  const valueKey = value ? `${value[0].toFixed(6)},${value[1].toFixed(6)}` : "";
  const prevValueKeyRef = useRef(valueKey);
  useEffect(() => {
    if (prevValueKeyRef.current === valueKey) return;
    prevValueKeyRef.current = valueKey;
    if (value) {
      const c = centerRef.current;
      if (Math.abs(value[0] - c[0]) > 1e-6 || Math.abs(value[1] - c[1]) > 1e-6) {
        setFocusTarget({ location: value, zoom: 16 });
      }
    }
  }, [valueKey, value]);

  const handleSearch = async () => {
    if (!q.trim()) return;
    if (noResultTimer.current) clearTimeout(noResultTimer.current);
    setMapNotice("");
    setSearching(true);
    try {
      // Scope the search with the selected city/province: a neighbourhood
      // name typed bare (e.g. «گلستان») exists in many cities, and the
      // selected context is what makes it resolve the exact one. A place
      // outside the province still resolves — the bounded search simply
      // falls back to the unbounded one.
      const outcome = await resolvePlace(q.trim(), "district", {
        provinceName,
        cityName,
      });
      if (outcome.status === "found") {
        setFocusTarget({ location: outcome.location, zoom: 15 });
        setQ("");
      } else {
        showNotice(NOTICE_TEXT[outcome.status]);
      }
    } finally {
      setSearching(false);
    }
  };

  const handleConfirmCenter = () => {
    onChange([roundCoord(center[0]), roundCoord(center[1])]);
  };

  // The confirm button is visible while the marker (map centre) differs from
  // the confirmed coordinates — including the create form before the first
  // confirmation, where it is the only way to register a location.
  const dirty =
    !value || !sameCoord(center[0], value[0]) || !sameCoord(center[1], value[1]);

  const initialCenter: LatLng = value ?? focusTarget?.location ?? DEFAULT_VIEW_CENTER;
  const initialZoom = value ? 16 : focusTarget?.zoom ?? DEFAULT_VIEW_ZOOM;

  const consultantIds = useMemo(() => located.map((p) => p.consultantId), [located]);
  const iconCache = useRef(new Map<string, L.DivIcon>());
  const iconFor = useCallback(
    (p: LocatedProperty) => {
      const color = consultantMarkerColor(p.consultantId, consultantIds);
      let icon = iconCache.current.get(color);
      if (!icon) {
        icon = makePinIcon(color);
        iconCache.current.set(color, icon);
      }
      return icon;
    },
    [consultantIds]
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={13} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="جستجوی مکان (محله، شهر، آدرس)…"
            className="w-full pr-8 pl-3 py-2 text-sm rounded-xl border border-border bg-input-background outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <button
          type="button"
          onClick={handleSearch}
          disabled={searching}
          className="px-3 py-2 rounded-xl bg-primary text-white text-sm flex items-center gap-1.5 hover:opacity-90 transition-opacity"
        >
          {searching ? <Loader2 size={13} className="animate-spin" /> : <Crosshair size={13} />}یافتن
        </button>
      </div>
      <p className="text-[11px] text-muted-foreground/80">برای جستجوی مکان به اتصال اینترنت نیاز است.</p>

      <div className="isolate relative h-64 rounded-2xl overflow-hidden border border-border">
        <MapContainer
          center={initialCenter}
          zoom={initialZoom}
          scrollWheelZoom
          dragging
          doubleClickZoom
          touchZoom
          className="zaminex-map-picker"
          style={{ height: "100%", width: "100%", cursor: "grab" }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitMapSize />
          <CenterTracker onCenter={onCenter} />
          {located.map((p) => (
            <Marker key={p.id} position={[p.lat, p.lng]} icon={iconFor(p)}>
              <Popup closeButton={false} maxWidth={260} minWidth={220} className="zaminex-popup">
                <PropertyMarkerPopupBody
                  title={p.title}
                  consultantName={p.consultantName}
                  lat={p.lat}
                  lng={p.lng}
                  status={p.status}
                  area={p.area}
                />
              </Popup>
            </Marker>
          ))}
          <Marker position={center} icon={centerMarkerIcon} interactive={false} />
          <FlyToLocation location={focusTarget?.location ?? null} zoom={focusTarget?.zoom ?? initialZoom} />
        </MapContainer>

        {/* Live marker coordinates — update in real time while the map moves. */}
        <div className="pointer-events-none absolute bottom-2 left-2 z-[1000] bg-white/95 backdrop-blur rounded-lg px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm font-mono">
          {center[0].toFixed(6)}, {center[1].toFixed(6)}
        </div>
        {value && (
          <div className="pointer-events-none absolute top-2 right-12 z-[1000] bg-white/95 rounded-lg px-2 py-1 text-[11px] text-emerald-700 font-semibold flex items-center gap-1 shadow-sm">
            <MapPin size={11} />موقعیت ثبت شد
          </div>
        )}
        {dirty && (
          <button
            type="button"
            onClick={handleConfirmCenter}
            className="absolute bottom-2 right-2 z-[1000] flex items-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-xs font-semibold text-white shadow-md hover:opacity-90 transition-opacity"
          >
            <Check size={13} />
            تایید موقعیت ملک
          </button>
        )}
        {mapNotice && (
          <div className="absolute inset-0 z-[1001] flex items-center justify-center pointer-events-none">
            <div className="bg-white/95 backdrop-blur rounded-lg px-3 py-1.5 text-xs text-destructive shadow-sm border border-border">
              {mapNotice}
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-nowrap items-center gap-3 overflow-x-auto whitespace-nowrap text-[11px] text-muted-foreground">
        <span>کشیدن نقشه برای جابه‌جایی مارکر</span>
        <span>·</span>
        <span>دکمه «تایید موقعیت ملک» برای ثبت نقطه</span>
        <span>·</span>
        <span>کلیک روی پین‌های رنگی برای اطلاعات ملک</span>
      </div>
    </div>
  );
}

export { PropertyMapPicker };
