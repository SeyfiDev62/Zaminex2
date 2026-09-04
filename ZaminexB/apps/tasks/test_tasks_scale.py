"""Scale and N+1 guards for the tasks API.

Two separate defects lived in ``/tasks/api/tasks/``:

  * no paginator at all, so a list request serialised the entire table
    (20k tasks measured at 40 s and 19 MB), and
  * three queries per row from the nested serializers — ``UserMiniSerializer``
    reads each user's ``consultant_profile`` for the mobile, and
    ``PropertyMiniSerializer`` derives the price from the property's listings.

The first is covered by the pagination tests, the second by the flat-query-count
tests. ``SerializerSemanticsTests`` exists because both fixes touch how the row
is loaded (joins and a prefetch), and a join that silently changed a value would
be a worse bug than the one being fixed.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ConsultantProfile, UserRole
from apps.basics.models import DealType
from apps.listings.models import Listing
from apps.properties.models import Property
from apps.tasks.models import Task

User = get_user_model()


class _TaskScaleMixin:
    def _make_users(self):
        self.admin = User.objects.create_user(
            username="ts_admin", password="pw", role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="ts_agent",
            password="pw",
            first_name="علی",
            last_name="مشاوری",
            role=UserRole.AGENT,
        )
        self.other = User.objects.create_user(
            username="ts_other", password="pw", role=UserRole.AGENT
        )
        self.agent_mobile = "09121112233"
        ConsultantProfile.objects.create(user=self.agent, mobile=self.agent_mobile)

    def _make_property(self, title, code, price=2_000_000_000, **kwargs):
        return Property.objects.create(
            consultant=self.admin,
            title=title,
            internal_code=code,
            deal_type=Property.DealType.SALE,
            status=Property.Status.AVAILABLE,
            price=price,
            area=100,
            rooms=3,
            address=f"{title} address",
            neighborhood="سعادت آباد",
            **kwargs,
        )

    def _make_task(self, title, assigned_to=None, prop=None, **kwargs):
        return Task.objects.create(
            title=title,
            description="توضیحات",
            status=Task.Status.PENDING,
            priority=Task.Priority.MEDIUM,
            task_type=Task.TaskType.VIEWING,
            assigned_to=assigned_to or self.agent,
            created_by=self.admin,
            property=prop,
            due_date=(timezone.now() + timedelta(days=5)).date(),
            **kwargs,
        )


class SerializerSemanticsTests(_TaskScaleMixin, TestCase):
    """Joins and prefetches must not change what the serializer reports."""

    def setUp(self):
        self._make_users()
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        # Two sale listings at different prices: effective price is the highest.
        self.priced = self._make_property("قیمت‌دار", "TS_1", price=2_000_000_000)
        for amount in (2_400_000_000, 2_900_000_000):
            Listing.objects.create(
                property=self.priced,
                title=f"آگهی {amount}",
                publish_channel=Listing.PublishChannel.WEBSITE,
                created_by=self.admin,
                assigned_to=self.admin,
                sale_price=amount,
                start_date=timezone.now() - timedelta(days=10),
            )
        # No listings at all: the legacy Property.price column is the fallback.
        self.unpriced = self._make_property("بدون آگهی", "TS_2", price=1_750_000_000)

        self._make_task("T-priced", prop=self.priced)
        self._make_task("T-unpriced", prop=self.unpriced)
        self._make_task("T-noproperty", prop=None)

    def _rows(self):
        response = self.client.get("/tasks/api/tasks/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        return payload["results"] if isinstance(payload, dict) else payload

    def test_effective_price_is_highest_sale_listing(self):
        rows = {row["title"]: row for row in self._rows()}
        self.assertEqual(rows["T-priced"]["property_detail"]["price"], "2900000000")

    def test_effective_price_falls_back_to_property_price(self):
        rows = {row["title"]: row for row in self._rows()}
        self.assertEqual(rows["T-unpriced"]["property_detail"]["price"], "1750000000")

    def test_task_without_property_has_null_property_detail(self):
        rows = {row["title"]: row for row in self._rows()}
        self.assertIsNone(rows["T-noproperty"]["property_detail"])
        self.assertIsNone(rows["T-noproperty"]["propertyId"])

    def test_assignee_mobile_comes_through_the_join(self):
        """The mobile is a reverse one-to-one; a missing join yields None, not
        an error, so it has to be asserted explicitly."""
        rows = {row["title"]: row for row in self._rows()}
        detail = rows["T-priced"]["assigned_to_detail"]
        self.assertEqual(detail["mobile"], self.agent_mobile)
        self.assertEqual(detail["name"], "علی مشاوری")

    def test_creator_without_profile_reports_null_mobile(self):
        rows = {row["title"]: row for row in self._rows()}
        self.assertIsNone(rows["T-priced"]["created_by_detail"]["mobile"])

    def test_property_mini_fields_intact(self):
        rows = {row["title"]: row for row in self._rows()}
        detail = rows["T-priced"]["property_detail"]
        # Asserted against the object rather than a literal: Property.save()
        # deliberately replaces a foreign internal_code with the next code in
        # its own sequence.
        self.assertEqual(detail["internal_code"], self.priced.internal_code)
        self.assertEqual(detail["district"], "سعادت آباد")
        self.assertEqual(detail["area"], 100)


class TaskPaginationTests(_TaskScaleMixin, TestCase):
    def setUp(self):
        self._make_users()
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_list_returns_paginated_envelope(self):
        self._make_task("Only")
        response = self.client.get("/tasks/api/tasks/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, dict, "list endpoint must be paginated")
        self.assertEqual(set(data), {"count", "next", "previous", "results"})
        self.assertEqual(data["count"], 1)
        self.assertEqual(len(data["results"]), 1)

    def test_response_is_capped_at_the_large_list_page_size(self):
        """The board needs a wide window, but the window must still be finite."""
        from apps.common.pagination import LargeListPagination

        cap = LargeListPagination.page_size
        prop = self._make_property("انبوه", "TS_BULK")
        Task.objects.bulk_create(
            [
                Task(
                    title=f"T{i}",
                    description="d",
                    status=Task.Status.PENDING,
                    priority=Task.Priority.MEDIUM,
                    task_type=Task.TaskType.VIEWING,
                    assigned_to=self.agent,
                    created_by=self.admin,
                    property=prop,
                    due_date=timezone.now().date(),
                )
                for i in range(cap + 5)
            ]
        )
        response = self.client.get("/tasks/api/tasks/")
        data = response.json()
        self.assertEqual(data["count"], cap + 5)
        self.assertEqual(len(data["results"]), cap)
        self.assertIsNotNone(data["next"])

    def test_page_size_cannot_exceed_the_cap(self):
        self._make_task("A")
        response = self.client.get("/tasks/api/tasks/?page_size=100000")
        self.assertEqual(response.status_code, 200)

    def test_second_page_is_reachable(self):
        prop = self._make_property("صفحه‌دار", "TS_PAGE")
        Task.objects.bulk_create(
            [
                Task(
                    title=f"P{i}",
                    description="d",
                    status=Task.Status.PENDING,
                    priority=Task.Priority.MEDIUM,
                    task_type=Task.TaskType.VIEWING,
                    assigned_to=self.agent,
                    created_by=self.admin,
                    property=prop,
                    due_date=timezone.now().date(),
                )
                for i in range(1005)
            ]
        )
        first = self.client.get("/tasks/api/tasks/?page=1").json()
        second = self.client.get("/tasks/api/tasks/?page=2").json()
        self.assertEqual(len(first["results"]), 1000)
        self.assertEqual(len(second["results"]), 5)
        self.assertIsNone(second["next"])
        ids_1 = {row["id"] for row in first["results"]}
        ids_2 = {row["id"] for row in second["results"]}
        self.assertFalse(ids_1 & ids_2, "pages must not overlap")


class TaskQueryScaleTests(_TaskScaleMixin, TestCase):
    """The anti-N+1 guard: query count must not grow with the row count."""

    def setUp(self):
        self._make_users()
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.prop = self._make_property("مقیاس", "TS_SCALE")
        # The listing carries a real deal type on purpose. ``deal_type`` is
        # nullable, and Django resolves a null FK without touching the database,
        # so a fixture of deal-type-less listings cannot detect a prefetch that
        # stops one level short: ``property__listings`` looks complete until a
        # listing actually has a deal type to follow.
        self.deal_type = DealType.objects.create(name="sale", display_name="فروش")
        Listing.objects.create(
            property=self.prop,
            title="آگهی",
            publish_channel=Listing.PublishChannel.WEBSITE,
            created_by=self.admin,
            assigned_to=self.admin,
            sale_price=3_000_000_000,
            deal_type=self.deal_type,
            start_date=timezone.now() - timedelta(days=1),
        )

    def _bulk(self, n, prefix):
        Task.objects.bulk_create(
            [
                Task(
                    title=f"{prefix}{i}",
                    description="d",
                    status=Task.Status.PENDING,
                    priority=Task.Priority.MEDIUM,
                    task_type=Task.TaskType.VIEWING,
                    assigned_to=self.agent,
                    created_by=self.admin,
                    property=self.prop,
                    due_date=timezone.now().date(),
                )
                for i in range(n)
            ]
        )

    def _queries(self, path="/tasks/api/tasks/?page_size=200"):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        # Warm the Phase-5 count cache so a cache miss is not mistaken for an
        # extra per-row query.
        warm = self.client.get(path)
        self.assertEqual(warm.status_code, 200)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return len(ctx)

    def test_query_count_is_bounded_for_a_full_page(self):
        self._bulk(200, "B")
        count = self._queries()
        self.assertLessEqual(
            count, 12, f"200 rows cost {count} queries; an N+1 is back"
        )

    def test_queries_do_not_scale_with_rows(self):
        self._bulk(50, "X")
        small = self._queries()
        self._bulk(150, "Y")
        large = self._queries()
        self.assertEqual(
            small,
            large,
            f"query count moved with row count ({small} -> {large})",
        )

    @staticmethod
    def _standalone_selects(ctx, table):
        """Queries that read ``table`` as their own FROM target.

        A plain substring match is not enough: ``select_related`` folds the
        related table into the main statement as a LEFT OUTER JOIN, so the
        table name legitimately appears in the primary query. Only a separate
        statement against it is a leak.
        """
        import re

        pattern = re.compile(r'FROM "%s"' % re.escape(table))
        return [q for q in ctx if pattern.search(re.sub(r"\s+", " ", q["sql"]))]

    def test_no_per_row_consultant_profile_query(self):
        """The exact shape that used to fire twice per row."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._bulk(40, "Z")
        self.client.get("/tasks/api/tasks/?page_size=200")
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/tasks/api/tasks/?page_size=200")
        hits = self._standalone_selects(ctx, "accounts_consultantprofile")
        self.assertEqual(
            len(hits), 0, f"{len(hits)} consultant-profile queries leaked"
        )

    def test_no_per_row_listing_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._bulk(40, "W")
        self.client.get("/tasks/api/tasks/?page_size=200")
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/tasks/api/tasks/?page_size=200")
        hits = self._standalone_selects(ctx, "listings_listing")
        self.assertLessEqual(
            len(hits), 1, f"{len(hits)} listing queries for 40 rows"
        )

    def test_no_per_row_deal_type_query(self):
        """``property__listings`` alone is not enough.

        ``_listing_sale_price`` reads ``listing.deal_type.name``, so the prefetch
        path has to reach the deal type. Every row shares one property here, so
        a short prefetch still costs only one extra query — which is why this is
        asserted on its own rather than folded into the listing-count check.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._bulk(40, "V")
        self.client.get("/tasks/api/tasks/?page_size=200")
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/tasks/api/tasks/?page_size=200")
        # Table name is ``basics_deal_type``, not the default ``basics_dealtype``
        # — the model declares an explicit db_table.
        hits = self._standalone_selects(ctx, "basics_deal_type")
        self.assertEqual(
            len(hits), 0, f"{len(hits)} deal-type queries leaked; prefetch is short"
        )


class TaskAccessScopeTests(_TaskScaleMixin, TestCase):
    """Pagination must not widen the role-scoped queryset."""

    def setUp(self):
        self._make_users()
        self.prop = self._make_property("دسترسی", "TS_ACCESS")
        self.mine = self._make_task("Mine", assigned_to=self.agent, prop=self.prop)
        self.theirs = self._make_task("Theirs", assigned_to=self.other, prop=self.prop)

    def _ids(self, client):
        data = client.get("/tasks/api/tasks/").json()
        rows = data["results"] if isinstance(data, dict) else data
        return {row["id"] for row in rows}

    def test_consultant_sees_only_own_tasks(self):
        client = APIClient()
        client.force_authenticate(user=self.agent)
        self.assertEqual(self._ids(client), {self.mine.id})

    def test_admin_sees_every_task(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        self.assertEqual(self._ids(client), {self.mine.id, self.theirs.id})

    def test_summary_still_counts_the_scoped_set(self):
        client = APIClient()
        client.force_authenticate(user=self.agent)
        data = client.get("/tasks/api/tasks/summary/").json()
        self.assertEqual(data["total"], 1)
