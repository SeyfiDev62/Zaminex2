"""GIN trigram indexes for the fuzzy search on the listing table.

Same reasoning as ``properties.0016``: ``common.0006`` enabled ``pg_trgm`` but
created no index, so the listing search sequenced through the whole table
computing ``word_similarity`` per row. Only ``title`` and ``description`` are
indexed here — the listing search's other two fields are ``property__title``
(covered by the property index) and ``assigned_to__username`` (a small lookup
table whose sequential scan is not the cost this fixes).
"""

from django.db import migrations

ZWNJ = "\u200c"

_TARGETS = [
    ("listings_listing", "title", "listings_listing_title_trgm_idx"),
    ("listings_listing", "description", "listings_listing_desc_trgm_idx"),
]


def _expression(column: str) -> str:
    # Matches apps.common.fuzzy_search._index_expression exactly.
    return f"upper(replace({column}::text, '{ZWNJ}'::text, ''::text))"


def _trgm_available(connection) -> bool:
    if connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        return cursor.fetchone() is not None


def create_indexes(apps, schema_editor):
    connection = schema_editor.connection
    if not _trgm_available(connection):
        return
    with connection.cursor() as cursor:
        for table, column, name in _TARGETS:
            cursor.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON {table} USING gin ({_expression(column)} gin_trgm_ops)"
            )


def drop_indexes(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for _, _, name in reversed(_TARGETS):
            cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("listings", "0007_alter_listing_priority_alter_listing_publish_channel_and_more"),
        ("common", "0006_pg_trgm_extension"),
    ]

    operations = [
        migrations.RunPython(create_indexes, reverse_code=drop_indexes),
    ]
