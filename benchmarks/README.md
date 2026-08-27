# Zaminex Performance Benchmarks

Reproducible baseline for the hot read paths, used to judge every later
performance phase (pagination, caching, query fixes) by numbers.

## Run

```bash
cd ZaminexB
# seed 1000 synthetic properties (+ related listings/tasks/follow-ups),
# measure, and clean up afterwards:
python manage.py benchmark

# options:
python manage.py benchmark --props 5000        # bigger dataset
python manage.py benchmark --runs 10           # more runs per path
python manage.py benchmark --skip-seed         # measure the existing (real) data
python manage.py benchmark --keep-data         # keep the seeded rows
python manage.py benchmark --out my-report.json
```

Notes:

- The benchmark uses Django's test client against the **current database**
  (full middleware + DRF stack, no network). With `--skip-seed` it needs an
  active ADMIN user.
- Seeded rows are marked (titles `…بنچمارک…`, internal codes `ZF_9xxxx`,
  users `bench_…`) and deleted again afterwards — a crashed run is detected
  and pre-cleaned on the next run.
- One warm-up request per path is discarded; `p50`/`p95`/`max` latency,
  payload size, SQL query count and DB time are reported per path.
- The default report is written to `benchmarks/reports/latest.json`
  (overwritten each run). The committed baseline lives at
  `benchmarks/reports/baseline-phase0.json`.

## Paths measured

| path | what it is |
|---|---|
| `properties-list-p1000` | the **current production pattern** (frontend fetches up to 1000 rows in one request) |
| `properties-list-page1-p20` | the **target pattern** (real server-side pagination, 20/page) |
| `properties-list-page50-p20` | deep page 50 (OFFSET cost — keyset-pagination trigger data) |
| `properties-search-p20` | fuzzy search (`?q=`), pg_trgm path |
| `properties-detail` | single property detail (full serializer) |
| `listings-list-p1000` / `page1-p20` / `search-p20` | same three patterns for listings |
| `dashboard-analytics` | the admin dashboard analytics endpoint |
| `property-report` | the property full-report endpoint (also feeds CSV/PDF export) |

## Phase 0 baseline

Sandbox: single vCPU-class container, PostgreSQL 16.2, Django 5.2,
dataset = 1000 properties / 2000 listings / ~333 tasks / ~333 follow-ups /
10 consultants, 5 runs per path. Absolute numbers are machine-dependent —
**compare a phase's "after" numbers against a same-machine "before" run**,
not against this table.

| path | p50 ms | p95 ms | payload KB | queries |
|---|---:|---:|---:|---:|
| properties-list-p1000 | 559 | 585 | 1330 | 15 |
| properties-list-page1-p20 | 44 | 114 | 27 | 15 |
| properties-list-page50-p20 | 44 | 45 | 27 | 15 |
| properties-search-p20 | 119 | 162 | 26 | 16 |
| properties-detail | 30 | 32 | 1 | 14 |
| listings-list-p1000 | 6602 | 6861 | 1143 | 8008 |
| listings-list-page1-p20 | 150 | 153 | 23 | 168 |
| listings-search-p20 | 230 | 249 | 23 | 169 |
| dashboard-analytics | 12362 | 12767 | 2 | ≥9000 |
| property-report | 38 | 38 | 3 | 19 |

### Findings that define the later phases

1. **`dashboard-analytics` runs ≥9000 SQL queries per request** (the 9000 is
   the query-log cap — the true count is higher) on only 1000 properties /
   2000 listings: classic N+1 in the analytics aggregation. → query
   optimisation + caching (Phase 4).
2. **`listings-list-p1000` runs 8008 queries for 1000 rows** (~8 per row —
   N+1 in the listing serializer's `property_detail`), 6.6 s, 1.1 MB. →
   slim list serializer + real pagination (Phase 1).
3. **`properties-list-p1000` vs `page1-p20`: 1330 KB vs 27 KB and ~12×
   latency** for the same endpoint — the payload size, not the query count,
   is the cost at 1000 rows. → slim list serializer (Phase 1).
4. **`properties-search-p20` ≈ 120 ms** at 1000 rows — the pg_trgm
   full-filter cost grows with data; count caching may help at scale
   (Phase 5).
5. **Deep pagination is not yet a problem** (page 50 ≈ page 1 at this
   size); keyset pagination stays Phase 7, triggered by measurements.

### Regression rule

A performance "regression" is: a new failure in the Django test suite
beyond the 49 pre-existing ones, **or** a >20% latency increase of a
baseline path on the same machine after a phase that is not supposed to
affect it.

## Phase 1 results — slim list serializer + real pagination

The list endpoints (`/properties/api/properties/`, `/listings/api/listings/`)
now answer the `list` action with a slim read-only serializer
(`PropertyListSerializer` / `ListingListSerializer`): the detail-only payload
(description, full gallery → replaced by `imageUrl`, appraisal report,
dynamic attributes, the per-row market/metric blocks, the creator profile,
`priceDetails`) is dropped, and the remaining per-row fields are served from
prefetched relations plus one batched query for the effective property price
(`PropertyMiniSerializer` now reads `effective_price_map` from the serializer
context). Detail, create and update responses are byte-for-byte the full
serializer — only list responses changed shape.

Frontend: the consultant «ملک‌های من» / «همه املاک» tabs
(`PropertiesListView`) moved from client-side filtering of the 1000-row fetch
to real server-side pagination (page/page_size + q + the same filter params
the admin list already uses); the card views read `imageUrl`. The read-only
`scope=all` list additionally honours `consultantId` (the tab's consultant
filter under server-side pagination — it only ever narrows what `scope=all`
already exposes).

### Before → after (same machine, 1000 seeded properties / 2000 listings)

| path | p50 ms | p50 Δ | payload KB | queries |
|---|---:|---:|---:|---:|
| properties-list-p1000 | 654.6 → **434.7** | −34% | 1352.7 → 1004.3 | 15 → 10 |
| properties-list-page1-p20 | 47.2 → **24.5** | −48% | 27.2 → 20.2 | 15 → 10 |
| properties-list-page50-p20 | 47.8 → **25.6** | −46% | 27.1 → 20.2 | 15 → 10 |
| properties-search-p20 | 73.1 → **55.9** | −24% | 26.4 → 20.3 | 16 → 11 |
| properties-detail (unchanged path) | 27.9 → **35.2**¹ | flat | 1.3 | 14 |
| listings-list-p1000 | 8284.8 → **367.6** | **−96%** | 1147.8 → 770.8 | **8008 → 12** |
| listings-list-page1-p20 | 174.1 → **42.1** | −76% | 23.1 → 15.5 | 168 → 12 |
| listings-search-p20 | 212.6 → **81.6** | −62% | 23.1 → 15.6 | 169 → 14 |
| dashboard-analytics (Phase 4 target) | 13759.9 → 13667.9 | flat | 4.1 | ≥9000 |
| property-report (unchanged path) | 42.8 → 46.5 | flat | 3.2 | 19 |

¹ `properties-detail` p50 is flat (27.9 → 29.4–35.2 ms, 14 queries both
ways); the p95 of a single 5-run sample picked up one sandbox stall and is
not a code-path change.

The headline: the listings list went from **8008 queries / 8.3 s** to **12
queries / 0.37 s** at 1000 rows — the N+1 is gone and the count is now
independent of page size. Guarded by
`PropertyListQueryCountTests` / `ListingListQueryCountTests` (query-count
flatness) and the shape tests in `apps/properties/test_list_serializer.py` /
`apps/listings/tests.py`.

## Phase 1 completion — spec gap closure

The Phase-1 spec has four items; three were delivered in the first commit,
one was under-delivered. This section documents the gap closure (report:
`benchmarks/reports/benchmark-phase1-complete.json`):

| spec item | status |
|---|---|
| slim list/detail serializers | ✅ + `imagesCount` now included (spec: "imagesCount + first thumbnail instead of the gallery"); query-free (prefetched gallery) |
| server-side pagination, lists | ✅ (first commit) |
| dashboard single-source, no `page_size=1000` fetches | ✅ closed: `refreshDashboard` no longer fetches the 1000-row property/listing lists; the analytics bundle is the single source (exact role-scoped KPIs already existed; **`locatedProperties` added** — the distribution maps' rows, one role-scoped query ≈9 ms at 1000 rows); the `listings` bulk fetch and the `scope=all&page_size=1000` tab fetch are gone (the tabs self-fetch) |
| `max_page_size` 1000 → 100 | ✅ closed: `StandardResultsSetPagination.max_page_size = 100`; a legacy `page_size=1000` request now answers 200 with ≤100 rows + the true `count` (measured below). `LargeListPagination` (1000) remains a deliberate opt-in used only by the small-table follow-ups endpoint; the combobox/map "every row" consumers page through in 100-row steps |

### Before → after (same machine, 1000 seeded properties / 2000 listings)

| path | p50 ms | notes |
|---|---:|---|
| properties-list-p1000 (**guard check**) | 434.7 → **57.5** | now clamped to 100 rows (102 KB) — measures the guard, not a 1000-row payload |
| listings-list-p1000 (**guard check**) | 367.6 → **61.2** | same: clamped to 100 rows (77 KB), 12 queries |
| properties-list-all-100loop (**new**) | **706.2** | the new "every visible property" pattern (10 × 100-row requests, 1032 KB, 110 queries) — replaces the bulk fetch for comboboxes/maps |
| properties-list-page1-p20 | 24.5 → 26.2 | flat |
| properties-search-p20 | 55.9 → 51.1 | flat |
| properties-detail | 35.2 → 30.3 | flat |
| listings-list-page1-p20 | 42.1 → 41.3 | flat |
| listings-search-p20 | 81.6 → 93.5 | trgm run-to-run variance |
| dashboard-analytics (Phase 4 target) | 13667.9 → ~14300–14900 | heavy 9000-query path, high variance in this container; single-shot measurement with the new code: 13676 ms — the `locatedProperties` addition costs ≈9 ms (measured separately). Payload 4 → 215 KB: the maps' rows moved into the bundle (total dashboard transfer dropped from ≈2.5 MB of bulk lists to ≈250 KB) |
| property-report | 46.5 → 45.7 | flat |

New tests: the 100-row clamp (`PropertyListPageSizeGuardTests`), exact
role-scoped `activeListings` (`test_dashboard_kpis_are_exact_not_row_count_limited`),
`locatedProperties` shape + scope (`test_dashboard_located_properties_shape_and_scope`),
`imagesCount` in the slim list.

## Phase 2 results — Redis infrastructure (fail-open)

Phase 2 is **infrastructure only**: no consumer is wired to the cache yet, so
every benchmark path is expected to be flat (report:
`benchmarks/reports/benchmark-phase2.json`) — and is (flat-to-faster, same
machine). The value of the phase is the safe foundation:

- `CACHES` (settings): `REDIS_URL` set → django-redis; absent → LocMem.
  `IGNORE_EXCEPTIONS: True` + 0.5 s socket timeouts → **fail-open** (a dead
  Redis is a cache miss, never a 500).
- `apps/common/cache_utils.py`: versioned keys (`zaminex:v1:<domain>:…` + a
  single `CACHE_VERSION`), JSON payloads with exact `Decimal` round-trip and
  `None`/Persian-text fidelity, fail-open `cache_get/set/delete`, and
  `cache_or_compute` with a per-key `SET NX EX` lock (thundering-herd
  protection) that fails fast when the lock state is unknown.
- `docker-compose.yml` (optional `redis:7`) + README section 10.
- Verified against a **live Redis 6.0.9**: set/get round-trip, cross-process
  visibility, `cache_or_compute` single-compute, and the roadmap acceptance
  test — kill Redis mid-run → the request still returns 200.
- DRF throttle counters confirmed to land in Redis (the Phase 3
  cross-worker rate-limit win needs no code change).

Tested by `apps/common/tests/test_cache_utils.py` (18 tests): key
versioning, JSON/Decimal round-trip, corrupt/foreign payload → miss,
fail-open helpers, dead-Redis → 200 request (real django-redis client against
a closed port), lock wait/timeout/disabled, and the CACHES builder.

## Phase 3 results — AI cache on Redis + global rate limits

Phase 3 makes the two "zero-code-change" Redis wins real and robust
(report: `benchmarks/reports/benchmark-phase3.json`). No benchmarked hot
path is affected — all paths are flat vs Phase 2 as designed.

- **AI cache on Redis**: `ai_service` now routes its description cache
  through the Phase-2 helpers — versioned key
  (`zaminex:v1:ai:desc:<entity>:<id>`), JSON payload (inspectable in
  redis-cli), fail-open read/write. With `REDIS_URL` set the hot layer is
  shared across all workers; the `AIInsightCache` DB row stays the
  persistent source of truth (and the fail-open fallback).
- **Thundering-herd protection**: the full-miss path runs under a per
  `(entity, fingerprint)` `SET NX EX` lock (90 s, outliving the 60 s LLM
  timeout; waiters block ≤15 s, then degrade to a parallel call). Concurrent
  cold requests for the same record now pay for exactly **one** model call.
- **Global rate limits**: DRF throttle counters were already cache-backed,
  so with Redis they are exact across workers — no code change. Verified
  live: 5 requests from one process + a 6th from another → 429, counter
  visible in Redis.

Tested by `apps/common/tests/test_phase3_benefits.py` (9 tests):
cross-process AI cache consistency (two backend handles on one shared
store), versioned/JSON key check, regeneration on changed data, concurrent
cold requests → exactly one LLM call (threaded, `TransactionTestCase`),
dead-cache fail-open (DB fallback), and the throttle counter shared across
two connections. Live verification against a real Redis 6.0.9 covered the
same scenarios end to end, including kill-Redis-mid-run → request served
from the DB row with zero LLM calls.

## Phase 4 results — caching the heavy aggregations

The four heaviest read-side computations are now behind short-TTL caches
(TTL + signal invalidation + stampede lock, all via the Phase-2/3
`cache_utils` infrastructure; report:
`benchmarks/reports/benchmark-phase4.json`):

| what | key | TTL | invalidation |
|---|---|---|---|
| property report (JSON/CSV/PDF/AI input) | `report:property:<id>` (one key holds all date-range variants) | 120 s | save/delete of the property, its listings/tasks/follow-ups/images |
| consultant scope report | `report:consultant:<user_id>` | 60 s | save/delete of the consultant's tasks/follow-ups/listings/properties (+ all admins — whole-portfolio scope) |
| neighbourhood price-stats map (detail page deviation index) | `stats:neighborhoods` | 60 s | property/listing saves (price/area/status inputs) |
| dashboard bundle (`/analytics/dashboard/`) | `dashboard:<user_id>` | 60 s | the same signals, for the affected users + admins |

Every entry is fail-open (dead cache → uncached behaviour, never an error —
verified live with a real connection-refused Redis), and the signals are
fail-open too (a cache outage during a save never breaks the write; the TTL
is the backstop).

### Before → after (same machine; the benchmark warm-up populates the caches)

| path | p50 ms | queries |
|---|---:|---:|
| dashboard-analytics | 12559.8 → **11.3** | 9000+ → 5 |
| property-report | 47.1 → **7.6** | 19 → 10 |
| properties-detail | 25.5 → **12.8** | 14 → 12 (stats map cached) |
| every other path | flat | flat |

Note the honest semantics: within a TTL the dashboard/report serve the
last-computed snapshot (that is the point); any save on the related models
drops the exact keys, so the next read is fresh. Live verification against a
real Redis 6.0.9: a second process serves the dashboard in 6 queries
(was 160) and the report in 11 (was 19); saving a listing makes the next
report recompute (20 queries); killing Redis mid-run still serves 200s.

Tested by `apps/common/tests/test_phase4_caching.py` (17 tests): hit/miss on
all four entries, range-variant coexistence, invalidation on every related
model (the roadmap acceptance: save a listing → report refreshes),
per-user isolation (scope reports, dashboards), and fail-open (dead cache
during reads *and* during saves).

## Phase 5 results — reference-data caches, poll caches, pagination COUNT

The three remaining QPS sources are cached (report:
`benchmarks/reports/benchmark-phase5.json`), all fail-open, all on the
Phase-2/3 cache infrastructure:

| what | key | TTL | invalidation |
|---|---|---|---|
| reference data: catalog, location tree, property/listing/search form schemas (`/basics/api/…`) | `catalog`, `location-tree`, `schema:<kind>:<type-pk>[::<deal-pk>]` | 10 min | any save/delete on the 12 reference models (types, usages, attributes, options, bindings, province/city/district) — an admin edit is visible on the next request; the TTL is the backstop |
| the notification bell poll (`/common/api/notifications/`) | `poll:notifications:<user_id>` | 10 s | the user's own mark-read drops their key immediately |
| the ticket unread badge poll (`/tickets/api/unread-count/` + the viewset action) | `poll:ticket-unread:<user_id>` | 10 s | `mark_read` drops the actor's key immediately |
| the list/search **COUNT** (`page_size`/`page` excluded from the key) | `count:<user_id>:<path>:<filters>` | 30 s | none — a ≤30 s stale *label* is the design (rows on the page are always a fresh slice) |

The COUNT cache is the subtle one, and it is implemented so a stale count can
never corrupt a response: the total is a *served* count (supplied to a
minimal paginator that drives the `count` field and next/previous links but
never re-COUNTs and never clamps the page slice). The page rows are always a
fresh bounded `LIMIT/OFFSET` slice, so a stale total can at most surface an
empty out-of-range page or an extra empty page that heals within the TTL —
never a truncated page. A freshly computed total (cold key) keeps the stock
404-on-out-of-range contract; a cached total is lenient about an out-of-range
page for exactly that reason. The Phase-1 flatness guard
(`ListingListQueryCountTests`) originally caught a first implementation that
let Django's `top = min(top, count)` clamp truncate a page from a stale
count; that is what the no-clamp slice fixes.

### Phase 5 run vs the committed Phase 0 baseline (same machine; cumulative
through Phases 1-5 — the Phase-5 contribution is the last column)

| path | p50 ms (P0 → P5) | queries (P0 → P5) | Phase-5 contribution |
|---|---:|---:|---|
| properties-list-p1000 | 559.2 → 52.9 | 15 → 9 | COUNT cached (−1) |
| properties-list-page1-p20 | 44.5 → 21.4 | 15 → 9 | COUNT cached (−1) |
| properties-search-p20 | 118.6 → 39.1 | 16 → 10 | the expensive trgm COUNT is skipped on warm runs (−1) |
| listings-list-p1000 | 6602.0 → 56.9 | 8008 → 11 | COUNT cached (−1) |
| listings-search-p20 | 230.2 → 59.1 | 169 → 13 | trgm COUNT skipped on warm runs (−1) |
| catalog (live, 1000-row dataset) | — | 45 → 5 | whole catalogue from the 10-min cache |
| location tree (live) | — | 8 → 5 | 10-min cache, invalidated on any geo save |
| notifications poll (live) | — | 7 → 5 | 10 s per-user cache |
| ticket unread poll (live) | — | 6 → 5 | 10 s per-user cache |
| dashboard-analytics / property-report | 12361.8 → 12.7 / 38.3 → 8.8 | 9000+ → 5 / 19 → 10 | unchanged (already cached in Phase 4) |

A targeted A/B on the search path (small dataset, where the COUNT is cheap):
cache ON cold 60 ms/11 q → warm 17 ms/10 q vs cache OFF 18 ms/11 q → 19 ms/11 q
— the saved COUNT is the difference, and it is the dominant cost at scale.

Tested by `apps/common/tests/test_phase5_caching.py` (19 tests): hit/miss on
every entry, per-type schema isolation, invalidation on type/attribute/
district saves **and deletes** (a deleted type's stale key is dropped),
per-user isolation of polls and counts, count isolation per filter, the
stale-count safety property (deleted rows → 200 with a shorter page, never a
500), and fail-open on a dead cache for every entry. Because Django's
`TestCase` rolls back the DB but not the cache, the affected pre-existing
basics test bases gained the shared `CacheClearingMixin`
(`apps/common/testing.py`) so cached payloads never leak across tests.

## Phase 5 completion — spec gap closure

The Phase-5 spec has three work items; all three were delivered in the first
commit. A spec-by-spec re-audit against the roadmap table found one
invalidation gap — the admin **reorder** of form fields persists through
`QuerySet.update()`, which bypasses the `post_save` signals that drive the
reference-cache invalidation, so a reordered field order would linger in the
10-minute schema cache instead of being visible on the next request. This
section documents the closure:

| spec item (roadmap) | status |
|---|---|
| form schemas (property-form / listing-form / search) + catalog + location tree — TTL 10 min + signal invalidation on the basics tables | ✅ (first commit); **gap closed**: `PropertyTypeAttributeViewSet.reorder` now drops the reference keys explicitly after its `update()`-based write (an empty/no-op payload leaves the cache alone); the reordered order is visible on the very next request, the TTL remains the backstop |
| poll endpoints (notifications + ticket unread-count) — per-user cache, TTL 5–10 s | ✅ (first commit): 10 s per user, own mark-read drops the key immediately; the SPA's 30 s multi-tab polling coalesces into one query per user per window |
| pagination COUNT cache — key (endpoint + scope + filters), TTL 30 s, especially under trgm search | ✅ (first commit): `StandardResultsSetPagination.count_cache_ttl = 30`, key `(user, path, filters)` with `page`/`page_size` excluded — active on properties, listings, tickets and the follow-ups large-list variant; the page rows stay a fresh bounded slice that a stale count can never truncate |
| tests: hit/miss + basics invalidation + per-user isolation + fail-open | ✅ (first commit, 19 tests) + **2 new**: reorder invalidates the schema cache; a no-op reorder keeps it warm |

Re-verified after the closure (same machine, 1000 seeded properties /
2000 listings, report: `benchmarks/reports/benchmark-phase5.json` — refreshed):

| path | p50 ms | queries | vs the committed Phase-5 run |
|---|---:|---:|---|
| properties-list-p1000 (guard check) | 50.3 | 9 | 52.9 / 9 — flat |
| properties-list-page1-p20 | 21.9 | 9 | 21.4 / 9 — flat |
| properties-search-p20 | 34.8 | 10 | 39.1 / 10 — flat |
| listings-list-p1000 (guard check) | 57.5 | 11 | 56.9 / 11 — flat |
| listings-search-p20 | 61.6 | 13 | 59.1 / 13 — flat |
| dashboard-analytics / property-report | 11.3 / 7.9 | 5 / 10 | 12.7 / 8.8, 5 / 10 — unchanged (Phase 4) |

Full suite after the closure: 644 tests (642 + 2 new), failure set identical
to the 50 pre-existing ones (0 new, 0 fixed) — the three pre-existing tests
that exercise the reorder endpoint still pass.
