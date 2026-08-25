import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Search, Crosshair, MapPin, Check, Loader2 } from "lucide-react";
import {
  IRAN_DEFAULT_CENTER,
  IRAN_DEFAULT_ZOOM,
  IRAN_PROVINCE_CENTERS,
  resolvePlaceCoordinates,
  type LatLng,
} from "../../lib/iranLocations";
import { apiFetch } from "../../lib/apiClient";
import { consultantMarkerColor } from "../../lib/consultantColors";
import {
  makePinIcon,
  PropertyMarkerPopupBody,
} from "./PropertyMarkerPopup";

// Leaflet's default marker icons don't resolve from bundlers; build one inline.
const centerMarkerIcon = L.divIcon({
  className: "zaminex-map-pin",
  // The fixed selection marker: always rendered at the map centre, so it
  // stays in the middle of the frame while the user drags the map around it.
  html: `<div style="display:flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:9999px;background:rgba(255,255,255,0.96);border:3px solid #0BB68A;box-shadow:0 4px 10px rgba(0,0,0,0.28);">
           <div style="width:9px;height:9px;border-radius:9999px;background:#0BB68A;"></div>
         </div>`,
  iconSize: [30, 30],
  iconAnchor: [15, 15],
});

function roundCoord(n: number): number {
  // Backend DecimalField(max_digits=9, decimal_places=6) rejects raw Leaflet
  // floats (13+ digits) with a confusing "no more than 9 digits" error.
  return Number(n.toFixed(6));
}

function sameCoord(a: number, b: number): boolean {
  return Math.abs(roundCoord(a) - roundCoord(b)) < 1e-9;
}

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
  const [searchNoResult, setSearchNoResult] = useState(false);
  const noResultTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // On edit the marker is already saved. Do not fly to the district centre on
  // first hydrate — that used to yank the camera off the real marker.
  const skipInitialFly = useRef(Boolean(value));
  const didHydrateLocation = useRef(false);

  // Live map centre — the position of the fixed marker. The coordinates
  // rendered under the map and the confirm-button state both follow this.
  const [center, setCenter] = useState<LatLng>(value ?? IRAN_DEFAULT_CENTER);
  const centerRef = useRef(center);
  centerRef.current = center;
  const onCenter = useCallback((c: LatLng) => setCenter(c), []);

  // All located properties, drawn as consultant-coloured markers so the
  // user can see the surroundings (and avoid registering on top of another
  // property — the backend rejects exact duplicates as well).
  const [located, setLocated] = useState<LocatedProperty[]>([]);
  useEffect(() => {
    if (!csrfToken) return;
    let cancelled = false;
    apiFetch("/properties/api/properties/?scope=all&page_size=1000", { method: "GET" }, csrfToken)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled) return;
        const rows = Array.isArray(data) ? data : (data?.results ?? []);
        setLocated(
          rows
            .filter((r: any) => r.latitude != null && r.longitude != null)
            .map((r: any) => ({
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
      })
      .catch(() => {});
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
        if (!value) setFocusTarget({ location: IRAN_DEFAULT_CENTER, zoom: IRAN_DEFAULT_ZOOM });
        return;
      }
      const kind = districtName ? "district" : cityName ? "city" : "province";
      const resolved = await resolvePlaceCoordinates(name, kind, { provinceName, cityName });
      if (cancelled) return;
      if (resolved) {
        const zoom = districtName ? 15 : cityName ? 12 : 8;
        setFocusTarget({ location: resolved, zoom });
      } else if (!value && provinceName) {
        // fallback to the province centre when a district/city lookup fails
        const p = IRAN_PROVINCE_CENTERS[provinceName];
        if (p) setFocusTarget({ location: p, zoom: 9 });
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
    setSearchNoResult(false);
    setSearching(true);
    try {
      // Scope the search with the selected city/province: a neighbourhood
      // name typed bare (e.g. «گلستان») exists in many cities, and the
      // selected context is what makes it resolve the exact one. A place
      // outside the province still resolves — the bounded search simply
      // falls back to the unbounded one.
      const resolved = await resolvePlaceCoordinates(q.trim(), "district", {
        provinceName,
        cityName,
      });
      if (resolved) {
        setFocusTarget({ location: resolved, zoom: 15 });
        setQ("");
      } else {
        // No match found: show a short notice over the map, then auto-dismiss.
        setSearchNoResult(true);
        noResultTimer.current = setTimeout(() => setSearchNoResult(false), 7000);
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

  const initialCenter: LatLng = value ?? focusTarget?.location ?? IRAN_DEFAULT_CENTER;
  const initialZoom = value ? 16 : focusTarget?.zoom ?? IRAN_DEFAULT_ZOOM;

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
        <div className="pointer-events-none absolute bottom-2 left-2 bg-white/95 backdrop-blur rounded-lg px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm font-mono">
          {center[0].toFixed(6)}, {center[1].toFixed(6)}
        </div>
        {value && (
          <div className="pointer-events-none absolute top-2 right-2 bg-white/95 rounded-lg px-2 py-1 text-[11px] text-emerald-700 font-semibold flex items-center gap-1 shadow-sm">
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
        {searchNoResult && (
          <div className="absolute inset-0 z-[1001] flex items-center justify-center pointer-events-none">
            <div className="bg-white/95 backdrop-blur rounded-lg px-3 py-1.5 text-xs text-destructive shadow-sm border border-border">
              نتیجه‌ای یافت نشد
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
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
