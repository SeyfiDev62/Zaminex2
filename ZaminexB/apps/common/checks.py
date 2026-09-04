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
