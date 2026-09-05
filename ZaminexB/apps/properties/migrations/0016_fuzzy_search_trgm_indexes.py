"""GIN trigram indexes for the fuzzy search on the property table.

``common.0006`` enabled the ``pg_trgm`` extension but never created any index,
so every fuzzy search was a sequential scan that computed ``word_similarity``
for every row of every searched column — measured at a flat 8 ms per 1,000
rows, i.e. linear in the size of the table.

The expression has to be written out exactly as Django compiles it. The search
helper annotates each searched field as ``Replace(field, ZWNJ, '')`` and then
filters it with ``__icontains``, which PostgreSQL sees as
``upper(replace(title::text, '‌'::text, ''::text)) LIKE ...``; the similarity
branch is written against the same ``Upper(Replace(...))`` expression. One
index per column therefore serves both branches. Writing the index against the
bare column would serve neither.

Wrapped in ``RunPython`` rather than declared as a ``GinIndex`` in the model
``Meta`` on purpose: on a database where ``pg_trgm`` is missing the
declarative form fails the whole migration, while fuzzy search already degrades
gracefully to its portable fallback. Skipping here keeps that contract.

``CONCURRENTLY`` so building these on a live 500k-row table does not block
writes; that is why the migration is non-atomic.
"""

from django.db import migrations

ZWNJ = "\u200c"

#: (table, column, index name)
_TARGETS = [
    ("properties_property", "title", "properties_property_title_trgm_idx"),
    ("properties_property", "internal_code", "properties_property_code_trgm_idx"),
    ("properties_property", "address", "properties_property_address_trgm_idx"),
    ("properties_property", "neighborhood", "properties_property_hood_trgm_idx"),
]


def _expression(column: str) -> str:
    # Matches apps.common.fuzzy_search._index_expression exactly.
    return f"upper(replace({column}::text, '{ZWNJ}'::text, ''::text))"


def _trgm_available(connection) -> bool:
    if connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
        )
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
        ("properties", "0015_alter_property_deal_type_and_more"),
        ("common", "0006_pg_trgm_extension"),
    ]

    operations = [
        migrations.RunPython(create_indexes, reverse_code=drop_indexes),
    ]
