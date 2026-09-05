"""Template tag that auto-resolves the hashed frontend assets.

Vite is configured to build straight into ``ZaminexB/static/frontend`` and to
emit a manifest at ``.vite/manifest.json`` mapping logical entries (e.g.
``src/main.tsx``) to their hashed JS/CSS files. This tag reads that manifest
once and renders the correct ``<script>`` / ``<link>`` tags, so you never edit
``base.html`` manually after a rebuild.

Usage in a template::

    {% load vite_assets %}
    {% vite_asset 'src/main.tsx' %}

If the manifest is missing (e.g. the frontend has not been built yet) the tag
renders an HTML comment instead of crashing, so the page still renders.

The same happens when the manifest is present but names files that are not on
disk. Vite always writes the two together, so that state means they have come
apart afterwards — typically a partial git operation that reverted the
manifest while leaving the newly built assets in place. It is verified here
rather than trusted because the failure it causes is invisible: both templates
render nothing but an empty ``<div id="root">`` and the SPA fills it, so a
404 on the entry script is a blank page with no exception, no log line and no
500 anywhere. See :func:`find_missing_assets`, which the system check in
``apps.common.checks`` shares so the two cannot disagree.
"""

import json
import logging
from pathlib import Path

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)

register = template.Library()


def _manifest_path():
    """Path to the Vite manifest; overridable via ``VITE_MANIFEST_PATH``."""
    return Path(
        getattr(
            settings,
            "VITE_MANIFEST_PATH",
            settings.BASE_DIR / "static" / "frontend" / ".vite" / "manifest.json",
        )
    )


def _frontend_root():
    """Directory the manifest's asset paths are relative to.

    Vite writes ``<outDir>/.vite/manifest.json`` and lists every asset
    relative to ``<outDir>``, so the root is the manifest's grandparent.
    Overridable via ``VITE_FRONTEND_ROOT`` for tests and unusual layouts.
    """
    override = getattr(settings, "VITE_FRONTEND_ROOT", "")
    if override:
        return Path(override)
    return _manifest_path().parent.parent


def _load_manifest():
    """Read and parse the Vite manifest.

    The manifest is a tiny file (~200 bytes), so it is read on every call —
    this is both cheap and fully correct (a fresh build is picked up on the very
    next request, with no caching subtleties). Returns ``{}`` on any error so
    the page still renders when the frontend has not been built yet.
    """
    path = _manifest_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _chunk_targets(chunk):
    """Every asset path one manifest entry points at (its JS plus its CSS)."""
    if not isinstance(chunk, dict):
        return []
    css = chunk.get("css") or []
    if not isinstance(css, (list, tuple)):
        css = [css]
    return [rel for rel in [chunk.get("file"), *css] if rel]


def find_missing_assets():
    """Verify the manifest against the files actually on disk.

    Returns:

    * ``None`` — there is no readable manifest, i.e. the frontend has not
      been built at all. Kept distinct from "built and complete" because a
      fresh checkout legitimately looks like this.
    * ``{}`` — every asset the manifest names is present.
    * ``{entry: [missing, ...]}`` — the manifest and the built assets have
      come apart; rendering these URLs would only produce 404s.

    Shared by the tag and the system check so "consistent" has exactly one
    definition in the codebase.
    """
    manifest = _load_manifest()
    if not manifest:
        return None
    root = _frontend_root()
    missing = {}
    for entry, chunk in manifest.items():
        absent = [rel for rel in _chunk_targets(chunk) if not (root / rel).is_file()]
        if absent:
            missing[entry] = absent
    return missing


@register.simple_tag
def vite_asset(entry: str = "src/main.tsx") -> str:
    """Render <script> and <link> tags for a Vite entry from the manifest."""
    manifest = _load_manifest()
    chunk = manifest.get(entry)
    if not chunk:
        return mark_safe(
            f"<!-- vite_asset: entry '{entry}' not found; run `npm run build` -->"
        )

    absent = [
        rel for rel in _chunk_targets(chunk) if not (_frontend_root() / rel).is_file()
    ]
    if absent:
        # Emitting URLs already known to 404 is worse than emitting nothing:
        # the page is blank either way, but this way the log names the cause
        # instead of a pile of 404s naming the symptom. The tag deliberately
        # does not raise — a template tag must not be able to take the site
        # down; apps.common.checks is what blocks a deploy instead.
        logger.error(
            "vite_asset: manifest entry %r points at %s, which is not under %s. "
            "The manifest and the built assets have come apart, so every page "
            "will render blank. Rebuild the frontend so both are written "
            "together: cd ZaminexF && npm run build",
            entry,
            ", ".join(absent),
            _frontend_root(),
        )
        return mark_safe(
            f"<!-- vite_asset: entry '{entry}' points at missing file(s) "
            f"{', '.join(absent)}; run `npm run build` -->"
        )

    # The manifest file paths are relative to static/frontend, and Django serves
    # that tree under STATIC_URL. Prefix with "frontend/".
    tags = []

    for css in chunk.get("css", []):
        tags.append(f'<link rel="stylesheet" href="{static("frontend/" + css)}">')

    js_file = chunk.get("file")
    if js_file:
        tags.append(
            f'<script type="module" src="{static("frontend/" + js_file)}"></script>'
        )

    return mark_safe("\n".join(tags))
