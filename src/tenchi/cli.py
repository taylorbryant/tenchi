"""The ``tenchi`` command-line interface.

Commands are intentionally few and reliable:

- ``tenchi new <name>`` scaffolds a new application with the prescribed
  structure.
- ``tenchi make feature <name>`` generates a feature skeleton; ``tenchi
  make use-case <feature> <name>`` generates a use-case stub and test.
  Generators create files and print wiring instructions — they never edit
  existing modules, because dependency wiring stays explicit and app-owned.
- ``tenchi routes`` prints the application's bound route table.
- ``tenchi openapi`` prints (or writes) the application's OpenAPI document.
- ``tenchi dev`` serves the application with uvicorn and reload.

The ``routes``, ``openapi``, and ``dev`` commands rely on the structural
convention that ``app/server/routes.py`` exposes ``routes`` and
``app/server/asgi.py`` exposes ``app``; both can be overridden by flag.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from .openapi import openapi_schema
from .routes import RouteGroup
from .scaffold import app_files, feature_files, use_case_files

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_DEFAULT_ROUTES = "app.server.routes:routes"
_DEFAULT_APP = "app.server.asgi:app"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        return _new(args.name)
    if args.command == "make":
        if args.artifact == "feature":
            return _make_feature(args.name)
        return _make_use_case(args.feature, args.name)
    if args.command == "routes":
        return _routes(args.target)
    if args.command == "openapi":
        return _openapi(args.target, args.title, args.version, args.output)
    return _dev(args.app, args.host, args.port, reload=not args.no_reload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tenchi",
        description="Contract-first, Python-native application framework.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a new Tenchi application")
    new_parser.add_argument("name", help="Application directory name, in snake_case")

    make_parser = subparsers.add_parser(
        "make", help="Generate application code from conventions"
    )
    make_subparsers = make_parser.add_subparsers(dest="artifact", required=True)
    feature_parser = make_subparsers.add_parser(
        "feature", help="Generate a feature skeleton under app/features/"
    )
    feature_parser.add_argument("name", help="Feature name, in snake_case")
    use_case_parser = make_subparsers.add_parser(
        "use-case", help="Generate a use-case stub and test in a feature"
    )
    use_case_parser.add_argument("feature", help="Existing feature name")
    use_case_parser.add_argument("name", help="Use case name, in snake_case")

    routes_parser = subparsers.add_parser(
        "routes", help="Print the application's bound routes"
    )
    routes_parser.add_argument(
        "--routes",
        dest="target",
        default=_DEFAULT_ROUTES,
        help="module:attribute of the RouteGroup (default: %(default)s)",
    )

    openapi_parser = subparsers.add_parser(
        "openapi", help="Print or write the application's OpenAPI document"
    )
    openapi_parser.add_argument(
        "--routes",
        dest="target",
        default=_DEFAULT_ROUTES,
        help="module:attribute of the RouteGroup (default: %(default)s)",
    )
    openapi_parser.add_argument(
        "--title",
        default=None,
        help="API title (default: the current directory name)",
    )
    openapi_parser.add_argument(
        "--version", default="0.1.0", help="API version (default: %(default)s)"
    )
    openapi_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write the document to this file instead of stdout",
    )

    dev_parser = subparsers.add_parser(
        "dev", help="Serve the application with uvicorn and reload"
    )
    dev_parser.add_argument(
        "--app",
        default=_DEFAULT_APP,
        help="module:attribute of the ASGI app (default: %(default)s)",
    )
    dev_parser.add_argument("--host", default="127.0.0.1")
    dev_parser.add_argument("--port", type=int, default=8000)
    dev_parser.add_argument(
        "--no-reload", action="store_true", help="Disable auto-reload"
    )

    return parser


def _new(name: str) -> int:
    if not _NAME.match(name):
        _fail(
            f"tenchi new: {name!r} is not a valid application name; "
            "use snake_case, such as 'my_app'"
        )
        return 1

    target = Path(name)
    if target.exists():
        _fail(f"tenchi new: {name}/ already exists")
        return 1

    _write_files(target, app_files(name))

    print(f"Created {name}/")
    print()
    print("Next steps:")
    print(f"  cd {name}")
    print("  uv sync")
    print("  uv run pytest")
    print("  uv run tenchi dev")
    return 0


def _make_feature(name: str) -> int:
    if not _NAME.match(name):
        _fail(
            f"tenchi make feature: {name!r} is not a valid feature name; "
            "use snake_case, such as 'notes'"
        )
        return 1

    features_root = Path("app") / "features"
    if not features_root.is_dir():
        _fail(
            "tenchi make feature: app/features/ not found; "
            "run this from an application root"
        )
        return 1

    feature_root = features_root / name
    if feature_root.exists():
        _fail(f"tenchi make feature: {feature_root} already exists")
        return 1

    _write_files(feature_root, feature_files(name))

    print(f"Created {feature_root}/")
    print()
    print("Next steps:")
    print(f"  1. Declare models in {feature_root}/schemas.py")
    print(f"  2. Declare ports in {feature_root}/ports.py")
    print(f"  3. Declare contracts in {feature_root}/contracts.py")
    print(f"  4. Generate use cases: tenchi make use-case {name} <use_case_name>")
    print(f"  5. Compose app.features.{name}.routes in app/server/routes.py")
    return 0


def _make_use_case(feature: str, name: str) -> int:
    if not _NAME.match(name):
        _fail(
            f"tenchi make use-case: {name!r} is not a valid use case name; "
            "use snake_case, such as 'create_note'"
        )
        return 1

    feature_root = Path("app") / "features" / feature
    if not feature_root.is_dir():
        _fail(
            f"tenchi make use-case: {feature_root} not found; "
            f"create it first with: tenchi make feature {feature}"
        )
        return 1

    files = use_case_files(feature, name)
    existing = [path for path in files if (feature_root / path).exists()]
    if existing:
        _fail(f"tenchi make use-case: {feature_root / existing[0]} already exists")
        return 1

    _write_files(feature_root, files)

    print(f"Created {feature_root}/use_cases/{name}.py")
    print(f"Created {feature_root}/tests/test_{name}.py")
    print()
    print("Next steps:")
    print(f"  1. Implement {name} and its test")
    print(f"  2. Bind it to a contract in {feature_root}/routes.py")
    return 0


def _routes(target: str) -> int:
    group = _load_route_group("tenchi routes", target)
    if group is None:
        return 1

    for line in format_routes(group):
        print(line)
    return 0


def _openapi(target: str, title: str | None, version: str, output: str | None) -> int:
    group = _load_route_group("tenchi openapi", target)
    if group is None:
        return 1

    document = openapi_schema(group, title=title or Path.cwd().name, version=version)
    rendered = json.dumps(document, indent=2)
    if output is None:
        print(rendered)
    else:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {output}")
    return 0


def _dev(app_target: str, host: str, port: int, *, reload: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        _fail("tenchi dev: uvicorn is not installed; add it with: uv add --dev uvicorn")
        return 1

    print(f"Serving {app_target} on http://{host}:{port}")
    uvicorn.run(app_target, host=host, port=port, reload=reload, app_dir=".")
    return 0


def _load_route_group(command: str, target: str) -> RouteGroup | None:
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        _fail(f"{command}: expected module:attribute, got {target!r}")
        return None

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        _fail(f"{command}: could not import {module_name!r}: {exc}")
        return None

    group = getattr(module, attribute, None)
    if not isinstance(group, RouteGroup):
        _fail(
            f"{command}: {target!r} is not a tenchi RouteGroup "
            f"(got {type(group).__name__})"
        )
        return None
    return group


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


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
