"""Offset pagination shared by contracts, use cases, and clients.

Subclass :class:`PageQuery` to add filters, use ``Page[Item]`` as the
contract response, and build results with :func:`page`:

    class ListTasksQuery(PageQuery):
        status: TaskStatus | None = None

    list_tasks_contract = contract(
        method="GET", path="/tasks", query=ListTasksQuery, response=Page[Task]
    )

    async def list_tasks(query: ListTasksQuery, context: AppContext) -> Page[Task]:
        items, total = await context.tasks.search(..., limit=query.limit,
                                                  offset=query.offset)
        return page(items, total=total, query=query)
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field


class PageQuery(BaseModel):
    """Base query model for paginated list endpoints.

    Subclasses add filters; override the fields to change the bounds or
    defaults for one endpoint.
    """

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Page[ItemT](BaseModel):
    """One page of results plus the total match count."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int


def page[ItemT](items: Sequence[ItemT], *, total: int, query: PageQuery) -> Page[ItemT]:
    """Build a :class:`Page` echoing the query's limit and offset."""
    return Page[ItemT](
        items=list(items), total=total, limit=query.limit, offset=query.offset
    )
