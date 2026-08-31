# Zaminex Investigation Log

Per-stage root-cause evidence. Essential findings are also reproduced in each
stage report.

## Stage 7 — precise province/city/district zoom (geocoding) + NO-MOVE fallback

### Diagnosis (evidence-first)

Nominatim reachability: `https://nominatim.openstreetmap.org` is **unreachable
from the sandbox** (`curl` → SSL_ERROR_SYSCALL on 443, empty reply on 80) while
general egress (PyPI) works — so REAL-mode walk-through was impossible; MOCK-mode
committed tests used instead.

Suspect classification (code + seed-data analysis):
- **(i) display-name vs OSM-name mismatch / thin coverage — CONFIRMED.** The
  seed tree (مازندران → ساری) exposes محله display names like `بلوار کشاورز`
  (a boulevard, not a neighbourhood), `مرکزی` ("central", generic), `آزادی`
  ("freedom", generic), `میدان ساعت` (a square), and `Daryashahr` (Latin
  script). These are exactly the inputs the resolver sends verbatim, and small
  محلهs have thin/no OSM coverage.
- **(ii) bounded viewbox too tight — CONFIRMED (contributing).** halfLat 1.6 /
  halfLng 2.6 can exclude edge-of-province places; widened to 2.2 / 3.5.
- **(iii) 1 req/s queue + 429 — NOT the primary cause.** The queue enforces
  ≥1.1 s and retries 429 once; only city+district geocode (province is static).
- **(iv) context wiring defect — DISPROVED.** `LocationSelect` disables city
  without province and district without city; `onProvinceChange` resets
  city+district, `onCityChange` resets district. No stale-parent path exists.

### Fix (iranLocations.ts + PropertyMapPicker.tsx)

- **Variant ladder** (`buildQueryVariants`): district → "d c p" → "d p" → "d";
  city → "c p" → "c". Free-text search (no `variants` option) keeps the single
  qualified query, bounded→unbounded, no ladder (today's behaviour).
- **Viewbox** widened to halfLat 2.2 / halfLng 3.5; `bounded=1` dropped when the
  variant is fully qualified (parents already in the query).
- **Acceptance rule** (`acceptsResult`): fully qualified → accept top hit;
  partially qualified → hard-reject only a clear mismatch (address names a
  different known Persian province); missing/English-only address → accept.
- **NO-MOVE fallback**: removed the picker's fly-to-province-centre fallback and
  the resolver's static-city-table fallback; on city/district failure the picker
  shows the short auto-dismissing `searchNoResult` notice and does not move.
  Province still zooms from the static table (no internet).
- **Tests**: added `vitest@^3.2.7` (dev-only) + `npm run test` script +
  `src/shared/lib/iranLocations.test.ts` (24 tests, fetch mocked, deterministic).

### Verification

- vitest: 24/24 pass (variant order, acceptance rule, 429 retry, offline → null,
  search single-query, province static-no-network).
- Django suite: 655/0. `npm run build` OK (bundle `main-BYUBwS0S.js`, CSS
  unchanged). No backend diff; no new runtime deps (vitest is dev-only).

## Environment rebuild (canonical — run verbatim after any sandbox wipe)

A wipe removes `.venv/`, `ZaminexF/node_modules/`, `/home/user/pg/` (PostgreSQL)
and the DB data dir; the repo stays but the local branch may reset to `fc63dbd`.
Recovery order:

1. **Git state** — `git fetch origin arena/01a05300-zaminex2` then
   `git reset --hard FETCH_HEAD` (history is linear; the remote carries every
   pushed stage commit). Preserve any uncommitted work to `/tmp/` first.
2. **Backend venv** — `python3 -m venv .venv` +
   `.venv/bin/pip install -r ZaminexB/requirements.txt`.
3. **PostgreSQL** — `pip download pgserver==0.1.4 --no-deps -d /home/user/pgwheel`;
   `unzip` the wheel, move `pgserver/pginstall` to `/home/user/pg/pginstall`;
   create the missing libpq symlink `ln -s libpq.so.5.16 libpq-084d956f.so.5.16`
   in `pginstall/lib`; fetch `REL_16_2` source and `make USE_PGXS=1
   PG_CONFIG=/home/user/pg/pginstall/bin/pg_config install` in
   `contrib/pg_trgm`; `initdb -D /home/user/pg/data --locale=C.UTF-8
   --encoding=UTF8 -U zaminex`; `pg_ctl -D /home/user/pg/data -o "-p 5432" start`;
   `CREATE DATABASE zaminex` + `ALTER USER zaminex WITH PASSWORD 'zaminex'`;
   restore `zaminex_backup.sql` after stripping the `\restrict`/`\unrestrict`
   lines; `manage.py migrate` + `manage.py check`.
4. **Frontend** — `npm install` in `ZaminexF` (installs the `vitest` devDependency
   added in Stage 7; run `npm run test` for the resolver suite).
5. **Baseline gate** — `manage.py test apps` must be **655/0** before any code
   edit. `export DATABASE_URL=postgres://zaminex:zaminex@127.0.0.1:5432/zaminex`
   and `export LD_LIBRARY_PATH=/home/user/pg/pginstall/lib` first.

## Stage 6 — map picker overlays / centre marker / helper line

### Reproduce / locate

The two overlay chips (lat/lon readout bottom-left, «موقعیت ثبت شد» badge
top-right) are `absolute` with `pointer-events-none` but **no z-index**, so
Leaflet's panes (z 200–700) and controls (z 1000) paint over them — they were
invisible. The confirm button already worked because it carries `z-[1000]`.

### Fix (minimal diff, PropertyMapPicker.tsx only)

- **Chips** — added `z-[1000]` to both (kept `pointer-events-none`).
- **Badge/zoom overlap** — Leaflet's zoom control defaults to `topright`, which
  the badge occupied. Leaflet's `zoomControl` Map option is boolean-only (no
  position object — verified in `leaflet-src.js`), so repositioning would need a
  `<ZoomControl position>` child. Chose the lighter, convention-preserving fix:
  offset the badge left (`right-2` → `right-12`) so it clears the ~30px control,
  keeping zoom at its default `topright` like every other map.
- **Centre marker** — replaced the inline circle `divIcon` with
  `makePinIcon("#0BB68A")` — the same teardrop pin template/size/anchor as the
  located-property markers (`iconSize [34,40]`, `iconAnchor [17,38]`), fixed
  green, so the pin tip lands on the map centre. No change to `position={center}`
  tracking or `CenterTracker`.
- **Helper line** — `flex flex-wrap gap-3` → `flex flex-nowrap items-center
  gap-3 overflow-x-auto whitespace-nowrap`; the three hints + separators are
  byte-identical, no wrap at any width, horizontal scroll at 320px.

### Verification

- Interaction logic (search, province/city/district resolution, confirm flow,
  value/centre sync, all `flyTo`) byte-identical — diff shows presentation only.
- Both create + edit forms share the single `PropertyMapPicker` (grep: two
  imports, one definition, no fork).
- Full suite 655 tests, 0 failures. `npm run build` OK.

## Stage 5 — consultant legend under admin map (scalability)

### Reproduce / locate

The admin «نقشه توزیع املاک» legend rendered `consultantColorLegend` rows as a
single `flex flex-wrap` row of chips with no bound or truncation: with 60+
consultants the legend became an unbounded wall, and long consultant names
pushed rows out of line.

### Fix (minimal diff, AdminDashboard.tsx only)

Chose **pattern (a) — bounded scrollable grid** (rejected (b) collapsible: it
hides the majority of a *reference* legend behind a click by default, and its
expanded state reintroduces an unbounded wall unless also bounded — i.e. (a)
plus a toggle). Layout container `flex flex-wrap` → `grid grid-cols-2
lg:grid-cols-3 gap-x-4 gap-y-1.5 max-h-36 overflow-y-auto rounded-lg border
border-border p-2`. Row anatomy preserved (dot + name + fa-IR count); added
`min-w-0` on the row, `min-w-0 truncate` + `title` on the name, `flex-shrink-0`
on dot/count so names truncate instead of overflow. Data logic
(`consultantColorLegend` useMemo) byte-identical; empty state untouched.

### Verification

- `git diff` shows only the legend container + row classes; `consultantColorLegend`
  useMemo unchanged (diff evidence).
- Full suite 655 tests, 0 failures. `npm run build` OK — bundle `main-DkmEwv2r.js`
  + CSS `main-B0kUzkT2.css` (new Tailwind utilities from the added classes).

## Stage 4 — maps default to Mazandaran (Bug 3)

### Reproduce / locate

Grep for `IRAN_DEFAULT_*` across `ZaminexF/src` found the default view constant
`IRAN_DEFAULT_CENTER = [35.6892, 53.0]` / `IRAN_DEFAULT_ZOOM = 5` (Tehran area,
country zoom) in `shared/lib/iranLocations.ts`. Three consumers of the constant:

1. `PropertyMapPicker` (create + edit) — CENTER (3×) + ZOOM (2×).
2. `PropertyDistributionMap` (dashboard) — CENTER (empty-points fallback) + a
   hardcoded `5` zoom literal in the same fallback.
3. `PropertyLocationsMap` (consultant) — CENTER (empty-points fallback) + the
   same hardcoded `5` zoom literal.

`PropertyLocationMap` (property detail) does **not** use the constant — it
centres on the property coordinates (`zoom=15`) and returns `null` when the
property has no coordinates, so it is a fly-to consumer, not a default-view one.

### Fix (minimal diff)

- Renamed `IRAN_DEFAULT_CENTER` → `DEFAULT_VIEW_CENTER` = `[36.4, 53.2]` and
  `IRAN_DEFAULT_ZOOM` → `DEFAULT_VIEW_ZOOM` = `8` (honest names + new values).
- Updated all three consumers; replaced the two hardcoded `5` empty-fallback
  zoom literals with `DEFAULT_VIEW_ZOOM` so the empty dashboard/consultant maps
  now show Mazandaran at the same zoom as the picker.
- Fly-to zooms (16/15/12/9/13/8 for real coordinates) untouched.

### Zoom math

Mazandaran province bounds used for the midpoint: **lat 35.9–36.9, lng
52.1–54.4** (latitude span ≈ 1.0°, longitude span ≈ 2.3°). Centre `[36.4, 53.2]`
is the exact midpoint: (35.9+36.9)/2 = 36.4 and (52.1+54.4)/2 = 53.25 ≈ 53.2.

Smallest surface is the picker panel (h-64 ≈ 256px). Visible latitude span at
zoom z ≈ 360 / 2^z → z=8 ≈ 1.4°, z=9 ≈ 0.7°. The province's ≈1.0° latitude span
fits z=8 with margin; z=9 would crop it.

### Verification

- `grep IRAN_DEFAULT` → 0 hits; all consumers reference the renamed constants.
- Full suite 655 tests, 0 failures. `npm run build` succeeds (new bundle
  `main-BitvxJ9J.js` replaces `main-Dyvngl7R.js`; CSS unchanged).

## Stage 3 — cache/AI: stop per-open LLM calls (fingerprint leak)

### Reproduce (evidence-first)

Instrumented the property AI data assembly (`apps/analytics/views._property_ai_data`)
and computed the fingerprint via `apps.analytics.ai_service.data_fingerprint` with a
mocked `timezone.now` stepped across a day boundary. Across all 10 seeded properties
(day 20 → day 21, stable business data):

- **Before fix** — 7/10 properties flipped fingerprint. Flipping normalized paths,
  and only these, were:
  - `charts.spatialScatter[i].x`  (integer)
  - `charts.avgLifespanByChannel[i].avgLifespan`  (float, same numeric value)
- Both are `today − listing.start_date` for active listings (`start_date` set,
  `end_date` null or future) — pure clock drift, not a business change.
- Consultant path (`_consultant_ai_data`) stable across the same boundary.
- `engagementHeatScore` (both `kpis.*` and `marketIndicators.*`) did **not** flip in
  any tested property — disproved as a suspect.

### Root cause

`_VOLATILE_KEYS` in `ai_service.py` dropped only top-level keys
(`daysOnMarket`, `engagementHeatmap`, `exposureTimeline`, …). The two chart arrays
are nested inside `charts`, so `_normalize_for_fingerprint` (which only filters on
key name at each dict level) did not remove their clock-derived fields. A day
boundary therefore changed the fingerprint → cache miss → a fresh LLM call on open.

### Fix (minimal diff)

Added `spatialScatter` and `avgLifespanByChannel` to `_VOLATILE_KEYS`. Because
`_normalize_for_fingerprint` drops any dict key in that set at every nesting level,
the two clock-derived chart arrays are now excluded from the fingerprint — matching
the existing treatment of `engagementHeatmap` / `exposureTimeline`.

### Verification

- After fix: all 10 properties + 3 consultants stable across the day boundary
  (0 flipping paths).
- Acceptance tests T1–T5 added (`AIAcceptanceTests` in
  `apps/analytics/tests/test_ai_service.py`), provider mocked at `_chat_completion` /
  `_urlopen`. AI module: 31 tests pass. Full suite: 655 tests, 0 failures.

### Poll consolidation (measured)

Already implemented + tested (`apps/common/tests/test_phase5_caching.py`).
Measured with `CaptureQueriesContext`:

- notifications poll: 6 → 4 queries (2nd hit cached); mark-read → unreadCount fresh
  (4 → 3).
- ticket unread-count poll: 5 → 4 queries (2nd hit cached); mark-read invalidates.
- Fail-open verified via dead-cache mock.

No defect found — no poll code changed this stage.

### Cache audit conclusion

The AI description cache was the only proven defect (fingerprint leak above).
All other cache entries (AIInsightCache DB layer, list COUNT, reference/schema,
property report, dashboard, both polls, session cached_db) verified correct with
their invalidation triggers and fail-open paths intact. See the stage report for
the full table.
