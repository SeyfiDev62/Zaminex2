"""Static-file serving for production, without a deployment dependency.

Django's development server only serves ``/static/`` while ``DEBUG`` is on.
``django/contrib/staticfiles/management/commands/runserver.py`` decides it in
one line::

    if use_static_handler and (settings.DEBUG or insecure_serving):
        return StaticFilesHandler(handler)

So with ``DEBUG=False`` and nothing else in front of the process, no handler is
installed at all, ``/static/`` is not in ``config/urls.py``, and every asset
404s. The failure is invisible: the page templates render ``200`` with an empty
``<div id="root">``, the hashed bundle 404s, and ``src/main.tsx`` — the only
thing that fills that div — never runs. The user sees a blank white page,
including on the login screen, with nothing in the logs to explain it.

This module closes that gap by installing the *same* class ``runserver`` uses
under ``DEBUG`` — ``StaticFilesHandler`` is part of Django itself, not a
third-party package — around the WSGI application, and adds the two things that
class deliberately leaves out:

* **``Cache-Control``.** ``StaticFilesHandler`` sends none, so the 1.4 MB
  hashed bundle would be re-downloaded on every single page view. That would
  only convert the blank page into an unusably slow one.
* **A clean 404 for path-traversal attempts.** ``StaticFilesHandler`` lets
  ``safe_join``'s ``SuspiciousFileOperation`` escape the WSGI callable, which a
  real server turns into a 500 plus a traceback. Today those URLs are a plain
  404; leaving them as 500s would be a regression, and an attacker scanning for
  traversal would fill the error log for free.

Nothing here depends on gunicorn, nginx or WhiteNoise, and nothing here is
coupled to them either: if a reverse proxy is added later it will answer
``/static/`` before the request reaches this process, and this handler simply
never runs. Deleting it is then a matter of removing the import from
``config/wsgi.py``.

WSGI only. ``config/asgi.py`` is untouched because ``ASGI_APPLICATION`` is not
configured; if this project is ever served over ASGI, wrap
``get_asgi_application()`` in ``django.contrib.staticfiles.handlers.
ASGIStaticFilesHandler`` the same way.
"""

import re

from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.exceptions import SuspiciousFileOperation
from django.http import Http404

#: Content-hashed assets can be cached forever: Vite derives the filename from
#: the file contents (``assets/[name]-[hash][extname]``), so any change to the
#: file produces a different URL and the old cache entry becomes unreachable
#: rather than stale.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

#: Everything else — ``favicon.ico``, the webfonts, the admin and DRF bundles —
#: has a stable URL and mutable contents, so it must revalidate. Five minutes
#: is short enough that a redeploy is picked up quickly and long enough to
#: collapse the repeat requests inside a browsing session.
REVALIDATE_CACHE_CONTROL = "public, max-age=300, must-revalidate"

#: Vite's output directory relative to ``STATIC_URL``. Matches ``base`` and
#: ``outDir`` in ``ZaminexF/vite.config.js``; if those move, this must move too.
#: A mismatch is safe — it only downgrades immutable caching to the short TTL.
HASHED_ASSET_PREFIX = "frontend/assets/"

#: Vite's default hash is eight base64url characters. Required *in addition* to
#: the directory prefix above: the prefix says "this is where hashed files go",
#: the pattern says "this particular file is one of them", so a hand-placed
#: file in that directory cannot be frozen in caches for a year. The length is
#: a floor rather than an exact match because the hash length is configurable.
_HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")


class CachedStaticFilesHandler(StaticFilesHandler):
    """Django's static handler, plus ``Cache-Control`` and a clean 404."""

    def __init__(self, application):
        super().__init__(application)
        # ``super().__init__`` resolves ``base_url`` from ``STATIC_URL``, so the
        # prefix is only meaningful once that has run.
        self.immutable_prefix = f"{settings.STATIC_URL}{HASHED_ASSET_PREFIX}"

    def cache_control_for(self, path):
        """Return the ``Cache-Control`` value for a static URL.

        Split out from :meth:`serve` so the caching policy can be asserted
        directly, without going through the WSGI stack.
        """
        if path.startswith(self.immutable_prefix) and _HASHED_ASSET.search(path):
            return IMMUTABLE_CACHE_CONTROL
        return REVALIDATE_CACHE_CONTROL

    def serve(self, request):
        try:
            response = super().serve(request)
        except SuspiciousFileOperation:
            # ``safe_join`` refused to resolve outside the static root. Answer
            # exactly as for a missing file: the path is not a valid asset, and
            # there is no reason to tell a scanner it was interesting. Without
            # this the exception escapes the WSGI callable and the server logs a
            # 500 traceback for every traversal probe.
            raise Http404("Static file not found.")
        response["Cache-Control"] = self.cache_control_for(request.path)
        return response


def static_files_handler(application):
    """Wrap ``application`` so ``/static/`` is served when ``DEBUG`` is off.

    A no-op under ``DEBUG``: ``runserver`` already wraps the application in
    ``StaticFilesHandler`` in that case, and a second wrapper would sit behind
    the first and never be reached.
    """
    if settings.DEBUG:
        return application
    return CachedStaticFilesHandler(application)
