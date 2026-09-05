import warnings

from django.db import migrations, transaction


def enable_pg_trgm(apps, schema_editor):
    """Enable the pg_trgm extension on PostgreSQL (used by fuzzy search).

    Only runs on PostgreSQL — the extension does not exist on other backends.

    A failure here must not take the rest of the migration run with it. On
    managed PostgreSQL the connecting role frequently cannot ``CREATE
    EXTENSION`` even though it owns every table, and pg_trgm is a performance
    feature the application degrades from gracefully rather than a schema
    requirement. Two things are needed to make that true:

    * the statement runs inside its own savepoint, because a failed statement
      in a migration otherwise aborts the surrounding transaction and every
      later statement fails with ``current transaction is aborted``;
    * the reason is surfaced as a warning instead of being swallowed, since a
      database left without the extension silently falls back to a much slower
      search path.
    """
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except Exception as exc:
        warnings.warn(
            "pg_trgm could not be enabled: %s. Fuzzy search will fall back to "
            "a slower path. Ask the database administrator to run "
            "'CREATE EXTENSION pg_trgm;' on this database." % exc,
            RuntimeWarning,
            stacklevel=2,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0005_alter_activitylog_options_and_more"),
    ]

    operations = [
        migrations.RunPython(enable_pg_trgm, reverse_code=migrations.RunPython.noop),
    ]
