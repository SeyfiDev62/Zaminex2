# Zaminex Investigation Log

Per-stage root-cause evidence. Essential findings are also reproduced in each
stage report.

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
