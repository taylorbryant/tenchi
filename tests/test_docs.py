import ast
import json
import re
from importlib.util import find_spec
from pathlib import Path
from textwrap import indent

DOCS = Path(__file__).parents[1] / "docs"
CONTENT = DOCS / "content"


def _pages() -> list[Path]:
    return sorted(CONTENT.glob("*.mdx"))


def _python_blocks(source: str) -> list[str]:
    return re.findall(r"^```python\n(.*?)^```$", source, re.MULTILINE | re.DOTALL)


def test_docs_are_a_static_next_application() -> None:
    package = json.loads((DOCS / "package.json").read_text())

    assert package["scripts"]["build"].endswith("next build --webpack")
    assert package["dependencies"]["next"]
    assert package["dependencies"]["react"]
    assert (DOCS / "bun.lock").is_file()
    assert (DOCS / "app" / "[[...slug]]" / "page.tsx").is_file()
    assert (DOCS / "public" / ".nojekyll").is_file()
    assert not (DOCS / "index.html").exists()


def test_docs_cover_the_framework_workflow() -> None:
    expected_pages = {
        "index",
        "getting-started",
        "concepts",
        "architecture",
        "comparisons",
        "contracts",
        "application",
        "server",
        "responses",
        "errors",
        "client",
        "pagination",
        "authentication",
        "execution",
        "testing",
        "openapi",
        "cli",
        "deployment",
        "reference",
        "stability",
    }
    pages = {page.stem for page in _pages()}
    registry = (DOCS / "lib" / "docs.ts").read_text()

    assert pages == expected_pages
    for page in expected_pages - {"index"}:
        assert f'path: "/{page}"' in registry

    openapi = (CONTENT / "openapi.mdx").read_text()
    quickstart = (CONTENT / "getting-started.mdx").read_text()
    assert "swagger_ui_route" in openapi
    assert "--diff-ref" in openapi
    assert "http://127.0.0.1:8000/docs" in quickstart


def test_docs_python_examples_are_valid_syntax() -> None:
    blocks = [block for page in _pages() for block in _python_blocks(page.read_text())]

    assert blocks
    for block in blocks:
        ast.parse(f"async def _example():\n{indent(block, '    ')}\n")


def test_docs_python_imports_reference_real_tenchi_modules() -> None:
    modules: set[str] = set()
    for page in _pages():
        for block in _python_blocks(page.read_text()):
            tree = ast.parse(f"async def _example():\n{indent(block, '    ')}\n")
            modules.update(
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("tenchi")
            )

    assert modules
    assert [module for module in sorted(modules) if find_spec(module) is None] == []
