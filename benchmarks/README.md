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
