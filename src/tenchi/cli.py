"""The ``tenchi`` command-line interface.

Commands are intentionally few and reliable:

- ``tenchi new <name>`` scaffolds a new application with the prescribed
  structure.
- ``tenchi routes`` prints the application's bound route table by importing
  the conventional ``app.server.routes:routes`` group.
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from .routes import RouteGroup
from .scaffold import app_files

_APP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tenchi",
        description="Contract-first, Python-native application framework.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a new Tenchi application")
    new_parser.add_argument("name", help="Application directory name, in snake_case")

    routes_parser = subparsers.add_parser(
        "routes", help="Print the application's bound routes"
    )
    routes_parser.add_argument(
        "--routes",
        dest="target",
        default="app.server.routes:routes",
        help="module:attribute of the RouteGroup (default: %(default)s)",
    )

    args = parser.parse_args(argv)
    if args.command == "new":
        return _new(args.name)
    return _routes(args.target)


def _new(name: str) -> int:
    if not _APP_NAME.match(name):
        _fail(
            f"tenchi new: {name!r} is not a valid application name; "
            "use snake_case, such as 'my_app'"
        )
        return 1

    target = Path(name)
    if target.exists():
        _fail(f"tenchi new: {name}/ already exists")
        return 1

    for relative_path, content in app_files(name).items():
        path = target / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    print(f"Created {name}/")
    print()
    print("Next steps:")
    print(f"  cd {name}")
    print("  uv sync")
    print("  uv run pytest")
    print("  uv run uvicorn app.server.app:app --reload")
    return 0


def _routes(target: str) -> int:
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        _fail(f"tenchi routes: expected module:attribute, got {target!r}")
        return 1

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        _fail(f"tenchi routes: could not import {module_name!r}: {exc}")
        return 1

    group = getattr(module, attribute, None)
    if not isinstance(group, RouteGroup):
        _fail(
            f"tenchi routes: {target!r} is not a tenchi RouteGroup "
            f"(got {type(group).__name__})"
        )
        return 1

    for line in format_routes(group):
        print(line)
    return 0


def format_routes(group: RouteGroup) -> list[str]:
    """Format a route group as aligned ``METHOD PATH STATUS use_case`` rows."""
    rows: list[tuple[str, str, str, str]] = []
    for item in group:
        contract = item.contract
        use_case = f"{item.use_case.__module__}.{item.use_case.__qualname__}"
        if contract.errors:
            codes = ", ".join(d.code for d in contract.errors)
            use_case = f"{use_case}  [{codes}]"
        rows.append((contract.method, contract.path, str(contract.status), use_case))

    if not rows:
        return ["no routes bound"]

    method_width = max(len(row[0]) for row in rows)
    path_width = max(len(row[1]) for row in rows)
    return [
        f"{method:<{method_width}}  {path:<{path_width}}  {status}  {use_case}"
        for method, path, status, use_case in rows
    ]


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
