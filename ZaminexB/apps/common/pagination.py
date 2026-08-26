from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    """
    Server-side pagination for large lists (properties, listings).
    Supports ?page= and ?page_size= (e.g. page_size=10 or 20).
    Frontend Pagination component expects total count via `count` field.

    Phase 1 guard: ``max_page_size`` is capped at 100 so an accidental or
    legacy ``page_size=1000`` request can no longer drag a thousand rows
    (and their serialized payload) through one response. Legitimate
    "give me every row" consumers (comboboxes, maps) page through the list
    in 100-row steps; the small-table follow-ups endpoint opts into
    ``LargeListPagination`` explicitly.
    """
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    page_query_param = "page"


class LargeListPagination(StandardResultsSetPagination):
    """Deliberate opt-in for endpoints whose consumers legitimately need
    the whole table in one response (the dashboard's scheduled-follow-ups
    widget reads every row to order overdue-then-recent exactly). Only the
    follow-ups endpoint uses it — keep it that way; the large tables must
    stay behind the 100-row guard of the base class.
    """

    page_size = 1000
    max_page_size = 1000
