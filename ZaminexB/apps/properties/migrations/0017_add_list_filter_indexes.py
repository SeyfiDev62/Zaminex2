"""Indexes for the property list endpoint's ordering and filters.

The list orders by newest first on every request and filters by status and
deal type almost as often. None of those columns had an index, so each page
of 100 cost a sequential scan of the whole table plus a top-N heapsort —
measured at 30,000 rows, 17.9 ms of scanning to return one page.

Written with ``AddIndexConcurrently`` rather than the generated ``AddIndex``:
the table this runs against in production is the one that needs the index
most, and a plain ``CREATE INDEX`` takes a ``SHARE`` lock that blocks every
write for as long as the build takes. Concurrently needs to run outside a
transaction, hence ``atomic = False``.
"""

from django.conf import settings
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("basics", "0004_attributecategory"),
        ("properties", "0016_fuzzy_search_trgm_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="property",
            index=models.Index(
                fields=["-created_at"], name="idx_property_created_at"
            ),
        ),
        AddIndexConcurrently(
            model_name="property",
            index=models.Index(
                fields=["status", "-created_at"], name="idx_property_status_created"
            ),
        ),
        AddIndexConcurrently(
            model_name="property",
            index=models.Index(
                fields=["deal_type", "-created_at"], name="idx_property_deal_created"
            ),
        ),
        AddIndexConcurrently(
            model_name="property",
            index=models.Index(
                fields=["property_type"], name="idx_property_type"
            ),
        ),
        AddIndexConcurrently(
            model_name="property",
            index=models.Index(fields=["area"], name="idx_property_area"),
        ),
        AddIndexConcurrently(
            model_name="property",
            index=models.Index(fields=["price"], name="idx_property_price"),
        ),
    ]
