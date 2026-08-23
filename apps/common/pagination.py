from collections import OrderedDict

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class DefaultPagination(PageNumberPagination):
    """Page-number pagination with a client-controllable, capped page size."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data) -> Response:
        # DRF only calls this after paginate_queryset(), so both are set.
        assert self.page is not None
        assert self.request is not None

        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("page", self.page.number),
                    ("pages", self.page.paginator.num_pages),
                    ("page_size", self.get_page_size(self.request)),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )


class TimestampCursorPagination(CursorPagination):
    """Stable pagination for large, frequently-written feeds.

    Prefer this over page numbers wherever rows are inserted while a client is
    paging — offsets shift, cursors don't.
    """

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-created_at"
