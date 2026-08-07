import ast
import json
import re
from importlib.util import find_spec
from pathlib import Path
from textwrap import indent

DOCS = Path(__file__).parents[1] / "docs"
CONTENT = DOCS / "content"
API_SNAPSHOT = Path(__file__).with_name("api_snapshot.txt")


def _pages() -> list[Path]:
    return sorted(CONTENT.glob("*.mdx"))


def _python_blocks(source: str) -> list[str]:
    return re.findall(r"^```python\n(.*?)^```$", source, re.MULTILINE | re.DOTALL)


def _api_snapshot_names() -> dict[str, set[str]]:
    modules: dict[str, set[str]] = {}
    current: set[str] | None = None
    for line in API_SNAPSHOT.read_text().splitlines():
        if line.startswith("# "):
            current = modules.setdefault(line.removeprefix("# "), set())
            continue
        if current is None or not line or line[0].isspace():
            continue
        match = re.match(
            r"^(?:class|def) ([A-Za-z_][A-Za-z0-9_]*)|"
            r"^([A-Za-z_][A-Za-z0-9_]*) (?:=|\()",
            line,
        )
        if match is not None:
            current.add(match.group(1) or match.group(2))
    return modules


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
        "idempotency",
        "existing-project",
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
        "jobs",
        "tasks",
        "tools",
        "tool-mcp",
        "evaluations",
        "testing",
        "webhooks",
        "production",
        "configuration",
        "database",
        "reliability",
        "observability",
        "preflight",
        "rate-limits",
        "deployment",
        "openapi",
        "cli",
        "agents",
        "mcp",
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
    agents = (CONTENT / "agents.mdx").read_text()
    assert "swagger_ui_route" in openapi
    assert "--diff-ref" in openapi
    assert "http://127.0.0.1:8000/docs" in quickstart
    assert "tenchi map --json" in agents
    assert "tenchi check --json" in agents
    assert "tenchi verify --base-ref" in agents
    assert "tenchi preflight --json" in agents
    assert "/llms.txt" in agents


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


def test_module_reference_covers_the_public_api_snapshot() -> None:
    reference = (CONTENT / "reference.mdx").read_text()
    root_block = next(
        block for block in _python_blocks(reference) if "from tenchi import (" in block
    )
    root_tree = ast.parse(root_block)
    root_names = {
        alias.name
        for node in ast.walk(root_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "tenchi"
        for alias in node.names
    }
    snapshot = _api_snapshot_names()

    expected_root_names = snapshot["tenchi"] | {"__version__"}
    assert root_names == expected_root_names, (
        f"package-root reference mismatch; missing "
        f"{sorted(expected_root_names - root_names)}, extra "
        f"{sorted(root_names - expected_root_names)}"
    )
    for module, public_names in snapshot.items():
        if module == "tenchi":
            continue
        row = re.search(
            rf"^\| `{re.escape(module)}` \| (?P<names>.+?) \|(?: .+? \|)?$",
            reference,
            re.MULTILINE,
        )
        assert row is not None, f"{module} is missing from the module reference"
        documented_names = set(
            re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`", row["names"])
        )
        assert documented_names == public_names, (
            f"{module} reference mismatch; missing "
            f"{sorted(public_names - documented_names)}, extra "
            f"{sorted(documented_names - public_names)}"
        )
