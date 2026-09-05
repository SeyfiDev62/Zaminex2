"""Tests for the property list indexes.

``Property.Meta`` declares ``ordering = ["-created_at"]``, which every list
endpoint applies, and the list UI filters by status, deal type, property type,
area and price. Before these indexes existed every one of those queries was a
sequential scan of the whole table plus a top-N heapsort: measured at 30,000
rows, 17.9 ms to return a single page of 100.

These tests assert both halves — that the indexes are declared consistently in
the model and the migration, and that the planner actually uses them.
"""

import importlib

from django.db import connection, transaction
from django.test import TestCase

from apps.properties.models import Property

migration = importlib.import_module(
    "apps.properties.migrations.0017_add_list_filter_indexes"
)

DECLARED = {
    "idx_property_created_at": ["-created_at"],
    "idx_property_status_created": ["status", "-created_at"],
    "idx_property_deal_created": ["deal_type", "-created_at"],
    "idx_property_type": ["property_type"],
    "idx_property_area": ["area"],
    "idx_property_price": ["price"],
}


def _explain(queryset):
    """EXPLAIN with the sequential scan disabled.

    The test fixture is small enough that PostgreSQL legitimately prefers a
    sequential scan, which says nothing about whether an index *can* serve the
    query — the property under test. Turning the alternative off isolates it.
    """
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        try:
            cursor.execute("EXPLAIN " + sql, params)
            return "\n".join(row[0] for row in cursor.fetchall())
        finally:
            cursor.execute("SET LOCAL enable_seqscan = on")


class IndexDeclarationTests(TestCase):
    def test_model_declares_every_index(self):
        declared = {index.name: index.fields for index in Property._meta.indexes}
        self.assertEqual(declared, DECLARED)

    def test_migration_declares_the_same_indexes_as_the_model(self):
        """Two sources of truth have to agree, or `makemigrations` drifts."""
        in_migration = {
            operation.index.name: operation.index.fields
            for operation in migration.Migration.operations
        }
        self.assertEqual(
            in_migration,
            {index.name: index.fields for index in Property._meta.indexes},
        )

    def test_migration_runs_outside_a_transaction(self):
        """CREATE INDEX CONCURRENTLY cannot run inside one."""
        self.assertFalse(migration.Migration.atomic)

    def test_the_default_ordering_is_indexed(self):
        """`ordering = ["-created_at"]` must have an index to serve it."""
        first = Property._meta.ordering[0]
        self.assertEqual(first, "-created_at")
        leading = [
            index.name
            for index in Property._meta.indexes
            if index.fields and index.fields[0] == "-created_at"
        ]
        self.assertIn("idx_property_created_at", leading)

    def test_the_indexes_really_exist_in_the_database(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes"
                " WHERE tablename = 'properties_property'"
            )
            present = {row[0] for row in cursor.fetchall()}
        for name in DECLARED:
            self.assertIn(name, present)


class IndexUsageTests(TestCase):
    def _test(self, name):
        with transaction.atomic():
            getattr(self, "_" + name)()

    def test_default_ordering_uses_an_index(self):
        self._test("default_ordering_uses_an_index")

    def _default_ordering_uses_an_index(self):
        plan = _explain(Property.objects.all()[:100])
        self.assertIn("Index Scan", plan)
        self.assertIn("idx_property_created_at", plan)
        self.assertNotIn("Sort", plan)

    def test_status_filter_uses_the_composite_index(self):
        self._test("status_filter_uses_the_composite_index")

    def _status_filter_uses_the_composite_index(self):
        plan = _explain(
            Property.objects.filter(status="AVAILABLE").order_by("-created_at")[:100]
        )
        self.assertIn("idx_property_status_created", plan)
        # The status has to be an index condition, not a post-scan filter.
        self.assertIn("Index Cond", plan)

    def test_area_range_can_use_its_index(self):
        self._test("area_range_can_use_its_index")

    def _area_range_can_use_its_index(self):
        plan = _explain(Property.objects.filter(area__gte=90, area__lte=130))
        self.assertIn("idx_property_area", plan)
