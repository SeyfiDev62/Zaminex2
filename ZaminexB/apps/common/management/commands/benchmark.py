"""manage.py benchmark — performance baseline for the Zaminex hot read paths.

Phase 0 of the performance roadmap: measure the current behaviour of the
read-heavy endpoints (property/listing lists, fuzzy search, dashboard,
property report) so that every later phase (server-side pagination, Redis
caching, …) can be judged by numbers, not impressions.

Design rules
------------
* **Reproducible**: the seeded dataset is generated from a fixed RNG seed and
  is deleted again afterwards (``--keep-data`` to keep it), so the baseline
  can be re-run on any database — the developer laptop and the production
  replica alike.
* **No behaviour changes**: the command only reads the same endpoints the
  frontend calls, through Django's test client (full middleware + DRF stack,
  no network, single user, sequential).
* **Honest metrics**: one warm-up request per path is discarded (connection
  pool, plan cache), then ``--runs`` measured runs. Reported per path:
  latency P50/P95/max/mean, payload bytes, SQL query count and DB time.
* **All production patterns measured**: the paginated list
  (``page_size=20`` + ``page``), the "fetch every row" pattern the
  comboboxes/maps use after the Phase-1 100-row guard
  (``*_all_100loop``: pages of 100 until done), search, detail, dashboard
  and report. The legacy ``*_p1000`` paths stay as guard checks — they
  request ``page_size=1000`` and measure how cheaply the server clamps it
  to 100 rows. Deep pages (``page=50``) are measured too, as the trigger
  data for future keyset pagination.

Usage
-----
    manage.py benchmark                     # seed 1000 props, 5 runs, clean up
    manage.py benchmark --props 5000        # bigger dataset
    manage.py benchmark --skip-seed         # measure existing (real) data
    manage.py benchmark --runs 10 --out bench.json
"""

import json
import random
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import django
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

# Fixed-seed Persian titles so the fuzzy-search path is realistic and the
# dataset (and therefore the numbers) is reproducible.
_TITLE_TEMPLATES = [
    "آپارتمان {area} متری {district}",
    "ویلا {area} متری {district}",
    "مغازه تجاری {area} متری {district}",
    "آپارتمان مرکز شهر {district}",
    "خانه ویلایی {area} متری {district}",
    "دفتر اداری {area} متری {district}",
]
_DISTRICT_NAMES = ["نیاوران", "زعفرانیه", "جردن", "شهرک غرب", "سعادت‌آباد"]

# Internal codes start with ZF_9 so the model's auto-generator (which only
# counts ^ZF_[1-9]{4}$) never collides with real codes; cleanup removes them.
_CODE_PREFIX = "ZF_9"

# A tiny valid 10x10 PNG so image rows reference a real file (the list
# serializer only builds URLs, but keep the media honest).
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000a0000000a0802000000025058ea"
    "0000001249444154789c63fccf800f30e1951db1d200412c0113b10a73130000000049454e44ae426082"
)


def _percentile(values, pct):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100 * (len(s) - 1)))))
    return round(s[k], 2)


class Command(BaseCommand):
    help = (
        "Benchmark the hot read paths (lists, search, dashboard, report) and "
        "write a JSON report for before/after performance comparison."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--props", type=int, default=1000,
            help="synthetic properties to seed (default 1000)",
        )
        parser.add_argument(
            "--runs", type=int, default=5,
            help="measured runs per path after warm-up (default 5)",
        )
        parser.add_argument(
            "--skip-seed", action="store_true",
            help="benchmark the existing data without seeding/cleaning",
        )
        parser.add_argument(
            "--keep-data", action="store_true",
            help="keep the seeded benchmark data after the run",
        )
        parser.add_argument(
            "--out", type=str, default=None,
            help="report path (default benchmarks/reports/benchmark-<ts>.json)",
        )

    # ------------------------------------------------------------------ #
    #  Seeding / cleanup
    # ------------------------------------------------------------------ #

    def _pre_cleanup(self):
        """Remove leftovers from a previous crashed/aborted run so the
        command is idempotent and re-runnable on a dirty database."""
        self._delete_bench_data()
        self.stderr.write("pre-cleanup of previous benchmark data done")

    def _delete_bench_data(self):
        """Delete seeded benchmark rows in a PROTECT-safe order.

        Tasks/follow-ups reference the property with SET_NULL (they survive a
        property delete as orphans) but PROTECT their user through
        created_by/consultant — so the order must be: tasks, follow-ups,
        properties (cascade removes listings/images), users. Orphaned rows
        (property already gone) are matched through their bench user.
        """
        from django.db.models import Q

        from apps.accounts.models import User
        from apps.followups.models import FollowUp
        from apps.properties.models import Property
        from apps.tasks.models import Task

        bench_user_task = Q(created_by__username__startswith="bench_") | Q(
            assigned_to__username__startswith="bench_"
        )
        for label, qs in (
            ("tasks", Task.objects.filter(
                Q(property__internal_code__startswith=_CODE_PREFIX) | bench_user_task
            )),
            ("follow-ups", FollowUp.objects.filter(
                Q(property__internal_code__startswith=_CODE_PREFIX)
                | Q(consultant__username__startswith="bench_")
            )),
            ("properties", Property.objects.filter(internal_code__startswith=_CODE_PREFIX)),
            ("users", User.objects.filter(username__startswith="bench_")),
        ):
            try:
                qs.delete()
            except Exception as exc:  # pragma: no cover - best effort
                self.stderr.write(f"warning: cleanup of {label} failed: {exc}")

    def _seed(self, n_props):
        from apps.accounts.models import User
        from apps.basics.models import (
            City,
            DealType,
            District,
            PropertyType,
            PropertyUsage,
            Province,
        )
        from apps.followups.models import FollowUp
        from apps.listings.models import Listing
        from apps.properties.models import Property, PropertyImage
        from apps.tasks.models import Task

        rng = random.Random(42)
        track = {"users": [], "refs": [], "png": None}

        # --- reference data: reuse what exists, create only what is missing
        usage = (
            PropertyUsage.all_objects.filter(name="residential").first()
            or PropertyUsage.objects.create(name="residential", display_name="مسکونی")
        )
        ptype = (
            PropertyType.all_objects.filter(name="bench_apartment").first()
            or PropertyType.objects.create(
                name="bench_apartment", display_name="آپارتمان بنچمارک",
                property_usage=usage,
            )
        )
        if ptype.name == "bench_apartment":
            track["refs"].append(ptype.pk)
        sale = (
            DealType.all_objects.filter(name="sale").first()
            or DealType.objects.create(name="sale", display_name="فروش")
        )
        rent = (
            DealType.all_objects.filter(name="rent").first()
            or DealType.objects.create(name="rent", display_name="رهن و اجاره")
        )
        province = (
            Province.all_objects.filter(name="bench_province").first()
            or Province.objects.create(name="bench_province", display_name="استان بنچمارک")
        )
        city = (
            City.all_objects.filter(name="bench_city").first()
            or City.objects.create(
                name="bench_city", display_name="شهر بنچمارک", province=province
            )
        )
        district = (
            District.all_objects.filter(name="bench_district").first()
            or District.objects.create(
                name="bench_district", display_name="محله بنچمارک", city=city
            )
        )
        for ref in (province, city, district):
            if ref.name.startswith("bench_"):
                track["refs"].append(ref.pk)

        # --- users: one benchmark admin + 10 consultants
        stamp = timezone.now().strftime("%H%M%S")
        admin = User.objects.create_user(
            username=f"bench_admin_{stamp}",
            password="bench-pass-12345",
            first_name="بنچ",
            last_name="مدیر",
            role="ADMIN",
        )
        track["users"].append(admin.pk)
        consultants = []
        for i in range(10):
            c = User.objects.create_user(
                username=f"bench_consultant_{stamp}_{i}",
                password="bench-pass-12345",
                first_name=f"مشاور{i}",
                last_name="بنچمارک",
                role="AGENT",
            )
            track["users"].append(c.pk)
            consultants.append(c)

        # --- one shared tiny PNG for the image rows
        png_path = Path(settings.MEDIA_ROOT) / "benchmarks" / "bench.png"
        try:
            png_path.parent.mkdir(parents=True, exist_ok=True)
            if not png_path.exists():
                png_path.write_bytes(_PNG_BYTES)
                track["png"] = str(png_path)
        except OSError:
            track["png"] = None

        # --- properties (individual creates: the activity signals fire,
        #     which is realistic — a growing database accumulates logs)
        now = timezone.now()
        for i in range(n_props):
            template = _TITLE_TEMPLATES[i % len(_TITLE_TEMPLATES)]
            area = 50 + (i * 7) % 300
            prop = Property(
                title=template.format(area=area, district=_DISTRICT_NAMES[i % len(_DISTRICT_NAMES)]),
                internal_code=f"{_CODE_PREFIX}{i:04d}",
                consultant=consultants[i % len(consultants)],
                property_type="APARTMENT",
                property_type_ref=ptype,
                property_usage=usage,
                deal_type="SALE",
                area=area,
                rooms=1 + i % 5,
                floor=i % 20,
                built_year=1990 + i % 35,
                address=f"آدرس بنچمارک پلاک {i}",
                neighborhood=_DISTRICT_NAMES[i % len(_DISTRICT_NAMES)],
                district=district,
                description=f"توضیحات بنچمارک ملک {i}. " * (1 + i % 3),
                latitude=round(35.5 + rng.random() * 0.3, 6),
                longitude=round(51.3 + rng.random() * 0.3, 6),
                status="AVAILABLE" if i % 4 else "RESERVED",
                is_shared=i % 5 == 0,
                owner_first_name="مالک",
                owner_last_name=f"بنچمارک{i % 50}",
                owner_phone="09121234567",
            )
            prop.save()
            if i % 5 == 0:  # 20% get 1-2 images
                for _ in range(1 + i % 2):
                    if track["png"]:
                        PropertyImage.objects.create(
                            property=prop, image="benchmarks/bench.png"
                        )

        # --- bulk related records (signals intentionally skipped: they are
        #     not part of the hot read paths being measured)
        props = list(
            Property.objects.filter(internal_code__startswith=_CODE_PREFIX)
            .order_by("id")
            .values_list("id", flat=True)
        )
        listing_rows = []
        for idx, pid in enumerate(props):
            owner = consultants[idx % len(consultants)]
            listing_rows.append(Listing(
                property_id=pid,
                title=f"فروش ملک بنچمارک {idx}",
                status="ACTIVE",
                publish_channel="WEBSITE",
                deal_type=sale,
                created_by_id=owner.pk,
                sale_price=1_000_000_000 * (10 + idx % 50),
                start_date=now - timedelta(days=idx % 60),
            ))
            listing_rows.append(Listing(
                property_id=pid,
                title=f"اجاره ملک بنچمارک {idx}",
                status="ACTIVE" if idx % 3 else "EXPIRED",
                publish_channel="INSTAGRAM",
                deal_type=rent,
                created_by_id=owner.pk,
                deposit=500_000_000 * (5 + idx % 30),
                monthly_rent=80_000_000 * (1 + idx % 10),
                start_date=now - timedelta(days=idx % 60),
                end_date=now + timedelta(days=30 - (idx % 60)),
            ))
        Listing.objects.bulk_create(listing_rows, batch_size=500)

        task_rows = []
        for idx, pid in enumerate(props):
            if idx % 3:
                continue
            owner = consultants[idx % len(consultants)]
            task_rows.append(Task(
                title=f"وظیفه بنچمارک {idx}",
                task_type="VIEWING" if idx % 2 else "NEGOTIATION",
                status="PENDING" if idx % 4 else "COMPLETED",
                priority="MEDIUM",
                assigned_to_id=owner.pk,
                created_by_id=owner.pk,
                property_id=pid,
                due_date=(now - timedelta(days=idx % 20)).date(),
            ))
        Task.objects.bulk_create(task_rows, batch_size=500)

        fu_rows = []
        for idx, pid in enumerate(props):
            if idx % 3:
                continue
            owner = consultants[idx % len(consultants)]
            fu_rows.append(FollowUp(
                title=f"پیگیری بنچمارک {idx}",
                follow_up_type="Call",
                contact_name=f"مشتری بنچمارک {idx % 50}",
                scheduled_at=now - timedelta(hours=idx % 72),
                consultant_id=owner.pk,
                property_id=pid,
                status="scheduled" if idx % 2 else "completed",
                probability=50 + idx % 50,
            ))
        FollowUp.objects.bulk_create(fu_rows, batch_size=500)

        return {
            "admin": admin,
            "track": track,
            "counts": {
                "properties": len(props),
                "listings": Listing.objects.filter(
                    property__internal_code__startswith=_CODE_PREFIX
                ).count(),
                "tasks": Task.objects.filter(
                    property__internal_code__startswith=_CODE_PREFIX
                ).count(),
                "followups": FollowUp.objects.filter(
                    property__internal_code__startswith=_CODE_PREFIX
                ).count(),
            },
        }

    def _cleanup(self, track):
        from apps.basics.models import City, District, PropertyType, Province

        self._delete_bench_data()
        # reference rows we created (reused rows are left untouched)
        for model, name in (
            (PropertyType, "bench_apartment"),
            (Province, "bench_province"),
            (City, "bench_city"),
            (District, "bench_district"),
        ):
            try:
                model.all_objects.filter(name=name).delete()
            except Exception:  # pragma: no cover
                pass
        png = track.get("png")
        if png:
            try:
                Path(png).unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass


    # ------------------------------------------------------------------ #
    #  Measurement
    # ------------------------------------------------------------------ #

    def _time_request(self, client, url):
        # The debug query log is a bounded deque (maxlen 9000); after
        # seeding it is full, so a per-request capture would be clipped to
        # zero. Clear it before every request so the counts are accurate.
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as ctx:
            t0 = time.perf_counter()
            response = client.get(url)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "status": response.status_code,
            "ms": elapsed_ms,
            "bytes": len(response.content),
            "queries": len(ctx.captured_queries),
            "db_ms": round(sum(float(q["time"]) for q in ctx.captured_queries) * 1000.0, 2),
        }

    def _time_request_loop(self, client, first_url, page_size=100, max_pages=50):
        """Measure the Phase-1 "fetch every row" pattern.

        The 100-row ``max_page_size`` guard makes a single bulk request
        impossible, so comboboxes/maps page through the list in 100-row
        steps. This times the whole loop (all pages) and reports the totals
        in the same sample shape as :meth:`_time_request`.
        """
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as ctx:
            t0 = time.perf_counter()
            total_bytes = 0
            status = None
            page = 1
            while page <= max_pages:
                url = first_url if page == 1 else f"{first_url}&page={page}"
                response = client.get(url)
                total_bytes += len(response.content)
                status = response.status_code
                try:
                    data = response.json()
                except Exception:
                    break
                if not isinstance(data, dict):
                    break
                rows = data.get("results") or []
                if len(rows) < page_size:
                    break
                page += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "status": status,
            "ms": elapsed_ms,
            "bytes": total_bytes,
            "queries": len(ctx.captured_queries),
            "db_ms": round(sum(float(q["time"]) for q in ctx.captured_queries) * 1000.0, 2),
        }

    def _aggregate(self, samples):
        latencies = [s["ms"] for s in samples]
        return {
            "status": samples[0]["status"],
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "max_ms": round(max(latencies), 2),
            "mean_ms": round(sum(latencies) / len(latencies), 2),
            "payload_bytes_mean": round(sum(s["bytes"] for s in samples) / len(samples)),
            "payload_bytes_max": max(s["bytes"] for s in samples),
            "queries_mean": round(sum(s["queries"] for s in samples) / len(samples), 1),
            "queries_max": max(s["queries"] for s in samples),
            "db_ms_mean": round(sum(s["db_ms"] for s in samples) / len(samples), 2),
        }

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opts):
        n_props = opts["props"]
        runs = max(1, opts["runs"])

        if opts["skip_seed"]:
            from apps.accounts.models import User
            from apps.followups.models import FollowUp
            from apps.listings.models import Listing
            from apps.properties.models import Property
            from apps.tasks.models import Task

            if not Property.objects.exists():
                self.stderr.write("error: no properties in the database; "
                                   "drop --skip-seed to seed benchmark data.")
                return
            admin = User.objects.filter(role="ADMIN", is_active=True).first()
            if admin is None:
                self.stderr.write("error: --skip-seed needs an active ADMIN user "
                                   "to measure the admin-scoped endpoints.")
                return
            dataset = {
                "properties": Property.objects.count(),
                "listings": Listing.objects.count(),
                "tasks": Task.objects.count(),
                "followups": FollowUp.objects.count(),
                "source": "existing",
            }
            seed = {"admin": admin, "track": {"users": [], "refs": [], "png": None}}
            detail_prop = (
                Property.objects.filter(listings__isnull=False).order_by("id").first()
            ) or Property.objects.order_by("id").first()
            report_prop = detail_prop
        else:
            from apps.properties.models import Property

            self._pre_cleanup()
            self.stdout.write(f"seeding {n_props} properties …")
            seed = self._seed(n_props)
            dataset = {**seed["counts"], "source": "seeded"}
            detail_prop = (
                Property.objects.filter(internal_code__startswith=_CODE_PREFIX, listings__isnull=False)
                .order_by("id")
                .first()
            )
            report_prop = detail_prop
            if report_prop is None:  # pragma: no cover
                self.stderr.write("error: seeding produced no properties.")
                return
        admin = seed["admin"]

        q = quote("آپارتمان")
        paths = {
            # Phase-1 guard check: a legacy page_size=1000 request is now
            # clamped to 100 rows — measures that the clamp is cheap.
            "properties-list-p1000": "/properties/api/properties/?page_size=1000",
            # target pattern (Phase 1) + deep page (OFFSET cost)
            "properties-list-page1-p20": "/properties/api/properties/?page=1&page_size=20",
            "properties-list-page50-p20": "/properties/api/properties/?page=50&page_size=20",
            "properties-search-p20": f"/properties/api/properties/?q={q}&page_size=20",
            "properties-detail": f"/properties/api/properties/{detail_prop.pk}/",
            # legacy page_size=1000 → clamped to 100 by the Phase-1 guard
            "listings-list-p1000": "/listings/api/listings/?page_size=1000",
            "listings-list-page1-p20": "/listings/api/listings/?page=1&page_size=20",
            "listings-search-p20": f"/listings/api/listings/?q={q}&page_size=20",
            "dashboard-analytics": "/common/api/analytics/dashboard/",
            "property-report": f"/api/reports/properties/{report_prop.pk}/",
        }
        # The "give me every row" production pattern after Phase 1: the
        # 100-row cap forces comboboxes/maps to page through the list.
        loop_paths = {
            "properties-list-all-100loop": "/properties/api/properties/?page_size=100",
        }

        # Django's test client defaults to the host "testserver", which is
        # not in this project's ALLOWED_HOSTS and would make every request
        # a 400 DisallowedHost — measure against "localhost" instead.
        client = Client(SERVER_NAME="localhost")
        client.force_login(admin)

        results = {}
        try:
            for name, url in {**paths, **loop_paths}.items():
                is_loop = name in loop_paths
                if is_loop:
                    self._time_request_loop(client, url)  # warm-up: discarded
                    samples = [
                        self._time_request_loop(client, url) for _ in range(runs)
                    ]
                else:
                    self._time_request(client, url)  # warm-up: discarded
                    samples = [self._time_request(client, url) for _ in range(runs)]
                results[name] = self._aggregate(samples)
                if results[name]["status"] != 200:
                    self.stderr.write(f"warning: {name} returned "
                                      f"{results[name]['status']}")
        finally:
            # A failing endpoint is itself a useful baseline result, but the
            # seeded data must never be left behind.
            if not opts["skip_seed"] and not opts["keep_data"]:
                self._cleanup(seed["track"])

        report = {
            "meta": {
                "benchmark": "zaminex-phase0-baseline",
                "generated_at": timezone.now().isoformat(),
                "django": django.get_version(),
                "dataset": dataset,
                "runs": runs,
                "method": (
                    "single user (admin), sequential requests through the "
                    "Django test client (full middleware + DRF, no network); "
                    "1 warm-up run discarded per path"
                ),
            },
            "results": results,
        }

        # ---- console table
        header = f"{'path':<28}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'KB':>8}{'queries':>9}"
        self.stdout.write("")
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for name, r in results.items():
            self.stdout.write(
                f"{name:<28}{r['p50_ms']:>9}{r['p95_ms']:>9}{r['max_ms']:>9}"
                f"{r['payload_bytes_mean'] / 1024:>8.1f}{r['queries_mean']:>9}"
            )
        self.stdout.write("")

        # ---- JSON report
        out = opts["out"]
        if not out:
            out = (
                Path(settings.BASE_DIR).parent
                / "benchmarks"
                / "reports"
                / "latest.json"
            )
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"report written: {out}"))

        if not opts["skip_seed"] and not opts["keep_data"]:
            self._cleanup(seed["track"])
            self.stdout.write("benchmark data cleaned up")
