# Zaminex Investigation Log

Per-stage root-cause evidence. Essential findings are also reproduced in each
stage report.

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
