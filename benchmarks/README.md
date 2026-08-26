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
