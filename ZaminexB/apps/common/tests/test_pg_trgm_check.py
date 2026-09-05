"""Tests for pg_trgm availability: the migration that installs it and the
system check that reports when it is missing.

A database restored from an old dump has never run ``common.0006``, so it has
no pg_trgm and every fuzzy search silently takes the slow path. These tests
cover both halves of the fix: the migration must install the extension without
being able to take the rest of the migration run down with it, and the check
must say so out loud.
"""

import importlib
from types import SimpleNamespace

from django.db import connection, transaction
from django.test import TestCase

from apps.common.checks import check_pg_trgm

pg_trgm_migration = importlib.import_module(
    "apps.common.migrations.0006_pg_trgm_extension"
)


class _PgFailingCursor:
    """Turns the CREATE EXTENSION into a statement PostgreSQL really rejects.

    The error has to come from the database, not from Python. A mock that
    simply raises never puts PostgreSQL into ``current transaction is
    aborted``, so a test built on one passes whether or not the migration
    uses a savepoint — it proves nothing. Replacing the statement with one the
    server rejects produces the real aborted-transaction state the savepoint
    exists to recover from.
    """

    def __init__(self, real):
        self._real = real

    def execute(self, sql, params=None):
        if "CREATE EXTENSION" in sql:
            sql = "SELECT * FROM zaminex_role_cannot_create_extension"
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PgTrgmCheckTests(TestCase):
    def test_silent_when_pg_trgm_is_usable(self):
        self.assertEqual(check_pg_trgm(None), [])

    def test_warns_when_the_extension_is_missing(self):
        # PostgreSQL DDL is transactional, so this is undone with the test.
        with connection.cursor() as cursor:
            cursor.execute("DROP EXTENSION IF EXISTS pg_trgm CASCADE")
        issues = check_pg_trgm(None)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].id, "pg_trgm.W001")

    def test_silent_when_the_database_cannot_be_reached(self):
        """A check must never be what stops a deploy."""

        class _NoDatabase:
            vendor = "postgresql"

            def cursor(self):
                raise Exception("connection refused")

        with transaction.atomic():
            from unittest.mock import patch

            with patch("django.db.connection", _NoDatabase()):
                self.assertEqual(check_pg_trgm(None), [])

    def test_ignores_non_postgresql_backends(self):
        from unittest.mock import patch

        with patch("django.db.connection", SimpleNamespace(vendor="sqlite")):
            self.assertEqual(check_pg_trgm(None), [])


class PgTrgmMigrationTests(TestCase):
    def test_creates_the_extension_when_it_is_absent(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP EXTENSION IF EXISTS pg_trgm CASCADE")
            cursor.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
            )
            self.assertIsNone(cursor.fetchone())

        pg_trgm_migration.enable_pg_trgm(None, connection.schema_editor())

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_is_idempotent_when_the_extension_already_exists(self):
        # The migration may legitimately run on a database that already has
        # the extension, so a second run must not fail.
        pg_trgm_migration.enable_pg_trgm(None, connection.schema_editor())
        pg_trgm_migration.enable_pg_trgm(None, connection.schema_editor())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_a_failing_create_does_not_abort_the_migration_run(self):
        """The failure has to stay contained.

        Migrations run inside a transaction. A failed statement without a
        savepoint leaves PostgreSQL in ``current transaction is aborted``, and
        every statement after it fails — so one role that cannot CREATE
        EXTENSION would block every unrelated migration in the run.
        """
        real_cursor = connection.cursor()
        editor = SimpleNamespace(
            connection=SimpleNamespace(
                vendor="postgresql",
                cursor=lambda: _PgFailingCursor(real_cursor),
            )
        )

        with transaction.atomic():
            with self.assertWarns(RuntimeWarning):
                pg_trgm_migration.enable_pg_trgm(None, editor)
            # The surrounding transaction must still be usable — this is the
            # assertion that fails if the savepoint is removed.
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone()[0], 1)
        real_cursor.close()

    def test_skips_non_postgresql_backends(self):
        editor = SimpleNamespace(
            connection=SimpleNamespace(
                vendor="sqlite",
                cursor=lambda: self.fail("must not touch a non-PostgreSQL DB"),
            )
        )
        pg_trgm_migration.enable_pg_trgm(None, editor)
