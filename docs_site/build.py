"""Build Tenchi's docs site from the repository's own markdown.

One script, one stylesheet, no JavaScript. The site duplicates nothing:
every page is a markdown file that already lives in the repo (README,
design notes, roadmap, changelog), rendered into static HTML with a
sidebar. Run from the repository root:

    uv run python docs_site/build.py          # writes docs_site/dist/

The generated site is flat — every page at the top level — so relative
links work from any base path (GitHub Pages project sites included).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from html import escape
from pathlib import Path

import markdown

ROOT = Path(__file__).parent.parent
OUT = Path(__file__).parent / "dist"

GITHUB = "https://github.com/taylorbryant/tenchi"


@dataclass(frozen=True)
class Page:
    source: str  # repo-relative markdown path
    slug: str  # output name without .html
    nav_title: str
    section: str


PAGES = (
    Page("README.md", "index", "Introduction", "Guide"),
    Page("docs/providers.md", "providers", "Providers", "Design notes"),
    Page("docs/events.md", "events", "Events & background work", "Design notes"),
    Page("docs/execution.md", "execution", "The execution model", "Design notes"),
    Page("docs/read-replicas.md", "read-replicas", "Read replicas", "Design notes"),
    Page("ROADMAP.md", "roadmap", "Roadmap", "Project"),
    Page("CHANGELOG.md", "changelog", "Changelog", "Project"),
)

# Repo-relative link targets that have a page on this site.
_PAGE_LINKS = {page.source: f"{page.slug}.html" for page in PAGES}
_PAGE_LINKS |= {f"./{source}": target for source, target in _PAGE_LINKS.items()}

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Tenchi</title>
<meta name="description"
  content="Tenchi: a contract-first Python framework for typed JSON APIs.">
<link rel="stylesheet" href="style.css">
</head>
<body>
<nav>
<p class="brand"><a href="index.html">Tenchi</a></p>
{nav}
<p class="external"><a href="{github}">GitHub</a> · <a href="https://pypi.org/project/tenchi/">PyPI</a></p>
</nav>
<main>
{body}
<footer>Tenchi · MIT licensed · <a href="{github}">source</a></footer>
</main>
</body>
</html>
"""


def build() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for page in PAGES:
        html = _render(page)
        target = OUT / f"{page.slug}.html"
        target.write_text(html, encoding="utf-8")
        written.append(target)
    style = OUT / "style.css"
    shutil.copyfile(Path(__file__).parent / "style.css", style)
    written.append(style)
    return written


def _render(page: Page) -> str:
    source = (ROOT / page.source).read_text(encoding="utf-8")
    body = markdown.markdown(
        source,
        extensions=["fenced_code", "tables", "toc"],
        extension_configs={"toc": {"anchorlink": False, "permalink": False}},
    )
    body = _rewrite_links(body)
    return _TEMPLATE.format(
        title=escape(page.nav_title),
        nav=_nav(active=page.slug),
        body=body,
        github=GITHUB,
    )


def _rewrite_links(body: str) -> str:
    """Point repo-relative hrefs at their site page, or at GitHub.

    External links, in-page anchors, and already-rewritten targets pass
    through untouched. Anything else that looks like a repo path (the
    examples, AGENTS.md, LICENSE) goes to the GitHub tree so no link on
    the site dead-ends.
    """

    def rewrite(match: re.Match[str]) -> str:
        target = match.group(1)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        path, _, fragment = target.partition("#")
        suffix = f"#{fragment}" if fragment else ""
        if path in _PAGE_LINKS:
            return f'href="{_PAGE_LINKS[path]}{suffix}"'
        return f'href="{GITHUB}/tree/main/{path.strip("/")}"'

    return re.sub(r'href="([^"]+)"', rewrite, body)


def _nav(active: str) -> str:
    sections: dict[str, list[str]] = {}
    for page in PAGES:
        marker = ' class="active"' if page.slug == active else ""
        sections.setdefault(page.section, []).append(
            f'<li><a href="{page.slug}.html"{marker}>{escape(page.nav_title)}</a></li>'
        )
    blocks = [
        f"<h2>{escape(section)}</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>"
        for section, items in sections.items()
    ]
    return "\n".join(blocks)


if __name__ == "__main__":
    for path in build():
        print(f"wrote {path.relative_to(ROOT)}")
