import math

from django.core.paginator import Page
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.common import cache_utils


class _ServedCountPaginator:
    """A minimal paginator whose total is *supplied*, not re-queried.

    The count drives the response's ``count`` field and the next/previous
    links, but is never recomputed (no per-request ``COUNT`` query) and never
    clamps a page slice — so a momentarily stale count can at most show an
    extra empty page or an empty out-of-range page, and can **never**
    truncate or mis-deliver rows (the rows are always a fresh bounded slice
    of the queryset).
    """

    def __init__(self, per_page, served_count):
        self.per_page = per_page
        self._served_count = served_count

    @property
    def count(self):
        return self._served_count

    @property
    def num_pages(self):
        # Page 1 stays valid even when the (possibly stale) count is 0,
        # matching Django/DRF's allow-empty-first-page behaviour.
        if self._served_count <= 0:
            return 1
        return max(1, math.ceil(self._served_count / self.per_page))

    def validate_number(self, number):
        """``Page.next_page_number`` / ``previous_page_number`` call this.

        The callers are already gated by ``has_next`` / ``has_previous``, so
        this only clamps into range — it never raises (a stale count must not
        turn a link into a 404).
        """
        if number < 1:
            return 1
        if number > self.num_pages:
            return self.num_pages
        return number


class StandardResultsSetPagination(PageNumberPagination):
    """
    Server-side pagination for large lists (properties, listings).
    Supports ?page= and ?page_size= (e.g. page_size=10 or 20).
    Frontend Pagination component expects total count via `count` field.

    Phase 1 guard: ``max_page_size`` is capped at 100 so an accidental or
    legacy ``page_size=1000`` request can no longer drag a thousand rows
    (and their serialized payload) through one response. Consumers that
    need every row (comboboxes, maps) page through in 100-row steps.

    Phase 5: the total ``COUNT`` — a full scan under filters, and a
    trigram-similarity scan under search — is the most expensive part of a
    list response, so it is served from a short-TTL cache keyed by
    (user, endpoint, filters). The cached total is a display/navigation
    figure with a ≤TTL staleness window; the page *rows* are always a fresh
    bounded slice and are never clamped by the count, so staleness can at
    most surface as an empty edge page that heals within the TTL — never as
    a wrong or truncated page. A freshly computed total (cold key) keeps the
    stock 404-on-out-of-range contract; a cached total is lenient about an
    out-of-range page for exactly that reason. Fail-open: a cache problem
    falls back to a plain fresh ``COUNT``.
    """
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    page_query_param = "page"

    # Phase 5: seconds to cache the total count; set 0 on a viewset to
    # disable it there.
    count_cache_ttl = 30

    # ------------------------------------------------------------------ #
    #  Count (Phase 5)
    # ------------------------------------------------------------------ #

    def _count_key(self, request):
        params = request.query_params.copy()
        params.pop(self.page_query_param, None)
        params.pop(self.page_size_query_param, None)
        return cache_utils.make_key(
            "count",
            getattr(request.user, "pk", None) or "anon",
            request.path,
            params.urlencode(),
        )

    def _served_count(self, queryset, request):
        """Return ``(count, is_fresh)`` from the short-TTL cache.

        ``is_fresh`` is True when the value was computed now (cold key or the
        cache is unavailable) and False when it was served from the cache
        (and may be up to the TTL stale).
        """
        ttl = self.count_cache_ttl
        if not ttl:
            return queryset.count(), True

        key = self._count_key(request)
        cached = cache_utils.cache_get(key)
        if isinstance(cached, int) and cached >= 0:
            return cached, False
        count = queryset.count()
        cache_utils.cache_set(key, count, ttl)
        return count, True

    def _resolve_page_number(self, request, num_pages, count_is_fresh):
        raw = request.query_params.get(self.page_query_param) or 1
        if raw in self.last_page_strings:
            return max(1, num_pages)
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise NotFound(
                self.invalid_page_message.format(page_number=raw, message="")
            )
        if number < 1:
            raise NotFound(
                self.invalid_page_message.format(page_number=number, message="")
            )
        if number > num_pages:
            if count_is_fresh:
                # Stock contract: a fresh total knows the page is out of range.
                raise NotFound(
                    self.invalid_page_message.format(page_number=number, message="")
                )
            # Cached total: the page may actually exist (rows added within the
            # TTL). Don't 404 — the fresh slice below simply returns no rows
            # if the page truly doesn't exist.
        return number

    # ------------------------------------------------------------------ #
    #  Pagination
    # ------------------------------------------------------------------ #

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        count, count_is_fresh = self._served_count(queryset, request)
        paginator = _ServedCountPaginator(page_size, count)
        page_number = self._resolve_page_number(
            request, paginator.num_pages, count_is_fresh
        )

        # Always a fresh, bounded slice — never clamped by the (possibly
        # stale) count, so rows are correct regardless of count staleness.
        bottom = (page_number - 1) * page_size
        top = bottom + page_size
        self.page = Page(list(queryset[bottom:top]), page_number, paginator)

        if paginator.num_pages > 1 and self.template is not None:
            # The browsable API should display pagination controls.
            self.display_page_controls = True

        return list(self.page)

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )


class LargeListPagination(StandardResultsSetPagination):
    """Deliberate opt-in for endpoints whose consumers legitimately need
    the whole table in one response (currently: follow-ups — the
    dashboard's "upcoming" widget reads the full small table to order
    overdue-then-recent). Keep it that way: the large tables (properties,
    listings) must stay behind the 100-row guard of the base class.
    Inherits the Phase-5 count cache.
    """

    page_size = 1000
    max_page_size = 1000
