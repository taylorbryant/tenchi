"""The docs site builds from the repo's markdown and links nowhere dead."""

import importlib
import re
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parent.parent


def build_site() -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "docs_site"))
    try:
        docs_build: Any = importlib.import_module("build")
        written = cast("list[Path]", docs_build.build())
    finally:
        sys.path.pop(0)
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in written
        if path.suffix == ".html"
    }


def test_site_builds_every_page_with_nav_and_styles() -> None:
    pages = build_site()

    assert set(pages) == {
        "index.html",
        "providers.html",
        "events.html",
        "execution.html",
        "read-replicas.html",
        "roadmap.html",
        "changelog.html",
    }
    for name, html in pages.items():
        assert "<nav>" in html and "style.css" in html, name
        assert "· Tenchi</title>" in html, name


def test_no_markdown_links_survive() -> None:
    """Every repo-relative link is rewritten to a site page or GitHub."""
    pages = build_site()

    for name, html in pages.items():
        for target in re.findall(r'href="([^"]+)"', html):
            assert not target.endswith(".md"), f"{name}: unrewritten {target}"
            assert (
                target.startswith(("http://", "https://", "#"))
                or target.endswith((".html", ".css"))
                or ".html#" in target
            ), f"{name}: suspicious link {target}"


def test_index_is_the_readme_and_cross_links_resolve() -> None:
    pages = build_site()

    assert "contract-first Python framework" in pages["index.html"]
    # README's link to the roadmap must point at the site page.
    assert 'href="roadmap.html"' in pages["index.html"]
    # Design notes cross-reference each other through site pages.
    assert 'href="events.html"' in pages["index.html"]
