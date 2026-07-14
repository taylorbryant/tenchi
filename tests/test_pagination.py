from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

from tenchi.contracts import contract
from tenchi.pagination import Page, PageQuery, page
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_client


class Item(BaseModel):
    name: str


@dataclass(frozen=True, slots=True)
class Context:
    pass


def test_page_echoes_the_query() -> None:
    query = PageQuery(limit=2, offset=4)

    result = page([Item(name="a"), Item(name="b")], total=9, query=query)

    assert result.total == 9
    assert result.limit == 2
    assert result.offset == 4
    assert [item.name for item in result.items] == ["a", "b"]


def test_page_query_bounds() -> None:
    assert PageQuery().limit == 20
    assert PageQuery().offset == 0

    with pytest.raises(ValidationError):
        PageQuery(limit=0)
    with pytest.raises(ValidationError):
        PageQuery(limit=101)
    with pytest.raises(ValidationError):
        PageQuery(offset=-1)


def test_page_query_subclass_adds_filters_and_overrides_bounds() -> None:
    from pydantic import Field

    class SearchQuery(PageQuery):
        term: str = ""
        limit: int = Field(default=5, ge=1, le=10)

    query = SearchQuery(term="milk")

    assert query.limit == 5
    with pytest.raises(ValidationError):
        SearchQuery(limit=11)


async def test_page_works_as_a_contract_response() -> None:
    class ItemsQuery(PageQuery):
        prefix: str = ""

    items_contract = contract(
        method="GET", path="/items", query=ItemsQuery, response=Page[Item]
    )

    catalog = [Item(name=f"item {i}") for i in range(7)]

    async def list_items(query: ItemsQuery, context: Context) -> Page[Item]:
        matches = [i for i in catalog if i.name.startswith(query.prefix)]
        return page(
            matches[query.offset : query.offset + query.limit],
            total=len(matches),
            query=query,
        )

    app = create_app(
        routes=route_group(route(items_contract, list_items)),
        context_factory=Context,
    )

    async with open_client(app) as client:
        result = await client.call(items_contract, query=ItemsQuery(limit=3, offset=5))

    assert isinstance(result, Page)
    assert isinstance(result.items[0], Item)
    assert result.total == 7
    assert [item.name for item in result.items] == ["item 5", "item 6"]
