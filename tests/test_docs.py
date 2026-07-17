import ast
from html.parser import HTMLParser
from pathlib import Path
from textwrap import indent

DOCS = Path(__file__).parents[1] / "docs"


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []
        self.python_blocks: list[str] = []
        self._language: str | None = None
        self._code: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.ids.append(element_id)
        if tag in {"a", "link"}:
            href = attributes.get("href")
            if href is not None:
                self.links.append(href)
        if tag == "pre":
            self._language = attributes.get("data-language")
            self._code = []

    def handle_data(self, data: str) -> None:
        if self._language is not None:
            self._code.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "pre" or self._language is None:
            return
        if self._language.startswith("python"):
            self.python_blocks.append("".join(self._code))
        self._language = None
        self._code = []


def _parse_page() -> _PageParser:
    parser = _PageParser()
    parser.feed((DOCS / "index.html").read_text())
    return parser


def test_docs_page_has_unique_working_internal_links() -> None:
    page = _parse_page()

    assert len(page.ids) == len(set(page.ids))
    for href in page.links:
        if href.startswith("#"):
            assert href.removeprefix("#") in page.ids


def test_docs_page_local_assets_exist() -> None:
    page = _parse_page()

    for href in page.links:
        if not href.startswith(("#", "https://")):
            assert (DOCS / href).resolve().is_file()


def test_docs_page_covers_the_framework_workflow() -> None:
    page = _parse_page()

    workflow = (
        "start",
        "contracts",
        "use-cases",
        "application",
        "errors",
        "client",
        "outcomes",
        "pagination",
        "workers",
        "testing",
        "cli",
    )

    assert set(workflow) <= set(page.ids)
    assert [page.ids.index(section) for section in workflow] == sorted(
        page.ids.index(section) for section in workflow
    )


def test_docs_python_examples_are_valid_syntax() -> None:
    page = _parse_page()

    assert page.python_blocks
    for block in page.python_blocks:
        ast.parse(f"async def _example():\n{indent(block, '    ')}\n")
