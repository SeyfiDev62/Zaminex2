"""Django system checks for ``apps.common``.

Registered from ``CommonConfig.ready()``, so every entry point that runs the
check framework picks them up: ``manage.py check``, ``manage.py check
--deploy``, ``manage.py test`` and the auto-check before ``runserver``.

Currently one check: that the built frontend the templates point at actually
exists. It lives here rather than in the template tag because a tag has to
degrade gracefully (it must never be able to take the site down), while a
deploy has to be *stoppped*. Splitting the two is what makes both possible.
"""

import sys

from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.common.templatetags.vite_assets import find_missing_assets

REBUILD_HINT = (
    "Rebuild the frontend so the manifest and the hashed assets are written "
    "together: cd ZaminexF && npm ci && npm run build"
)


def _under_test_runner() -> bool:
    """Whether we are running inside ``manage.py test``.

    The same idiom ``config/settings.py`` already uses to scope its
    test-only database name. Needed because the test runner forces
    ``DEBUG=False``, which is otherwise the signal for "this is production".
    """
    return "test" in sys.argv


@register()
def check_frontend_assets(app_configs, **kwargs):
    """Fail when the templates would reference frontend files that are absent.

    Both page templates render nothing but an empty ``<div id="root">``, and
    ``src/main.tsx`` fills it — so a missing entry script is a blank page with
    no exception and no 500 to alert anyone. Nothing else in the stack catches
    it either: ``collectstatic`` exits 0 because it only copies files that
    exist, and it never reads the manifest.

    Two distinct failures, deliberately different severities:

    * **No manifest** — the frontend was never built. Normal on a fresh
      checkout, so it is a warning while developing; in production it is an
      error, because shipping it means shipping a blank site.
    * **Manifest present but pointing at absent files** — the manifest and the
      build output have come apart. Always an error: there is no state in
      which this is anything but broken.
    """
    missing = find_missing_assets()

    if missing is None:
        message = (
            "The Vite manifest is missing, so no frontend assets can be "
            "resolved and every page will render blank."
        )
        if settings.DEBUG or _under_test_runner():
            return [
                Warning(
                    message,
                    hint=REBUILD_HINT,
                    id="vite_assets.W001",
                )
            ]
        return [
            Error(
                message,
                hint=REBUILD_HINT,
                id="vite_assets.E001",
            )
        ]

    if not missing:
        return []

    detail = "; ".join(
        f"{entry} -> {', '.join(paths)}" for entry, paths in sorted(missing.items())
    )
    return [
        Error(
            "The Vite manifest references frontend files that do not exist: "
            f"{detail}. The manifest and the built assets have come apart, so "
            "every page will render blank with no error to explain it.",
            hint=REBUILD_HINT,
            id="vite_assets.E002",
        )
    ]


@register()
def check_pg_trgm(app_configs, **kwargs):
    """Warn when fuzzy search is running without a usable pg_trgm.

    ``apply_fuzzy_search`` falls back to a pure-Python scan when pg_trgm
    cannot score Persian text. That fallback is correct — the results are the
    same — but it reads the whole table and scores it in Python, so it is the
    difference between a 40 ms search and a 20 second one. Nothing surfaces
    it: the site works, the results are right, and no exception is raised.

    Two very different causes need two very different fixes, so the probe
    tells them apart rather than reporting one undifferentiated "search is
    slow":

    * **extension missing** — the database predates ``common.0006``, which is
      exactly what a database restored from an old dump looks like. Fixed by
      running the pending migrations.
    * **extension present but unusable** — the database's LC_CTYPE is the
      plain "C" locale, where pg_trgm classifies every non-ASCII letter as
      non-alphanumeric and ``show_trgm('آپارتمان')`` returns ``{}``. No amount
      of migrating fixes this; the database has to be recreated with a
      UTF-8 locale.

    Deliberately a warning rather than an error: a slow search is not a broken
    site, and a check that blocks ``migrate`` would prevent running the very
    migration that fixes the first case.
    """
    from django.db import connection

    if getattr(connection, "vendor", "") != "postgresql":
        return []

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
            )
            installed = cursor.fetchone() is not None
            if not installed:
                return [
                    Warning(
                        "The pg_trgm extension is not installed on this "
                        "database, so fuzzy search runs the slow Python "
                        "fallback instead of using trigram indexes.",
                        hint=(
                            "Run the pending migrations (python manage.py "
                            "migrate), or have a superuser run "
                            "'CREATE EXTENSION pg_trgm;' on this database."
                        ),
                        id="pg_trgm.W001",
                    )
                ]
            # Installed is not the same as usable: probe the same way
            # apply_fuzzy_search decides which path to take.
            cursor.execute("SELECT show_trgm(%s)", ["آپارتمان"])
            row = cursor.fetchone()
            usable = bool(row and row[0])
    except Exception:
        # No database yet (first migrate), no permission to read the
        # catalogue, or a connection that is simply not up. None of those is
        # this check's business, and a check must never be what stops a deploy.
        return []

    if usable:
        return []

    return [
        Warning(
            "pg_trgm is installed but cannot tokenize Persian text on this "
            "database, so fuzzy search runs the slow Python fallback. The "
            "database's LC_CTYPE is almost certainly the plain 'C' locale, "
            "where every non-ASCII letter counts as a word separator and "
            "show_trgm('آپارتمان') returns an empty array.",
            hint=(
                "Recreate the database with a UTF-8 locale (for example "
                "CREATE DATABASE ... TEMPLATE template0 LC_CTYPE 'C.UTF-8') "
                "and restore the data into it. Migrations cannot fix this."
            ),
            id="pg_trgm.W002",
        )
    ]


@register()
def check_pending_migrations(app_configs, **kwargs):
    """Warn when the database schema is behind the code.

    ``zaminex_backup.sql`` is a snapshot, not a guarantee: it records the
    migrations that existed when it was taken. Importing it into an empty
    database therefore produces a site that *looks* healthy — it starts, the
    home page renders, most queries work — and is missing whole tables.
    Verified against the shipped dump: 36 tables instead of 44, and
    ``SELECT ... FROM tickets_ticket`` fails with "relation does not exist",
    so every ticket screen 500s while nothing else has complained.

    ``runserver`` prints its own reminder, but a WSGI deployment never runs
    ``runserver``, and neither ``migrate`` nor ``check`` says anything. This
    is the entry point that does.

    A warning rather than an error for the same reason as pg_trgm: an error
    would block ``migrate``, which is the thing that fixes it.
    """
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    try:
        loader = MigrationLoader(connection, ignore_no_migrations=True)
    except Exception:
        # No database yet, or no connection. Not this check's problem.
        return []

    plan = []
    for leaf in loader.graph.leaf_nodes():
        for node in loader.graph.forwards_plan(leaf):
            if node not in plan:
                plan.append(node)
    pending = [
        f"{app}.{name}"
        for app, name in plan
        if (app, name) not in loader.applied_migrations
    ]
    if not pending:
        return []

    shown = ", ".join(pending[:5])
    if len(pending) > 5:
        shown += f" and {len(pending) - 5} more"
    return [
        Warning(
            f"The database is missing {len(pending)} migration(s): {shown}. "
            "Tables and columns the code expects may not exist yet, so parts "
            "of the site will fail with 'relation does not exist' until this "
            "is run.",
            hint="python manage.py migrate",
            id="migrations.W001",
        )
    ]
