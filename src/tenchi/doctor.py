"""Architecture checks for ``tenchi doctor``.

Doctor enforces the dependency direction that keeps Tenchi apps honest:

- Domain and schema code must not import infrastructure or the HTTP runtime.
- Use cases may import schemas, ports, the app context, and shared errors —
  never concrete infrastructure or server composition.
- Routes bind contracts to use cases but must not import infrastructure.
- Shared code must not depend on features.
- Infrastructure implements ports; it must not import use cases, routes,
  contracts, or server composition.
- Server composition is the root: it may import anything.

Checks are static: modules under ``app/`` are parsed with ``ast``, imports
are resolved (including relative imports), classified by the prescribed
structure, and validated against the rules above. Test modules are exempt.
Absence of optional structure is fine; only misplacement and forbidden
dependencies are findings.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

Category = str

_STRUCTURE = (
    "app/__init__.py",
    "app/features/__init__.py",
    "app/server/__init__.py",
    "app/server/asgi.py",
    "app/server/context.py",
    "app/server/routes.py",
)

_FEATURE_KINDS: dict[str, Category] = {
    "schemas": "schemas",
    "domain": "schemas",
    "ports": "ports",
    "contracts": "contracts",
    "routes": "routes",
    "use_cases": "use_cases",
    "tests": "tests",
}

_HTTP_RULES: dict[Category, str] = {
    "http_runtime": "must not import the HTTP runtime",
    "tenchi_runtime": "must not import the Tenchi server or client runtime",
}

# Forbidden import targets per source category, with the rule each enforces.
_RULES: dict[Category, dict[Category, str]] = {
    "schemas": {
        "infra": "domain and schema code must not import infrastructure",
        "server": "domain and schema code must not import server composition",
        "context": "domain and schema code must not import the app context",
        **{k: f"domain and schema code {v}" for k, v in _HTTP_RULES.items()},
    },
    "ports": {
        "infra": "ports describe needs; they must not import infrastructure",
        "server": "ports must not import server composition",
        "context": "ports must not import the app context",
        **{k: f"ports {v}" for k, v in _HTTP_RULES.items()},
    },
    "contracts": {
        "infra": "contracts must not import infrastructure",
        "server": "contracts must not import server composition",
        "context": "contracts must not import the app context",
        "use_cases": "contracts must not import use cases",
        **{k: f"contracts {v}" for k, v in _HTTP_RULES.items()},
    },
    "use_cases": {
        "infra": "use cases must not import concrete infrastructure",
        "server": (
            "use cases may import the app context (app.server.context) "
            "but no other server composition module"
        ),
        "routes": "use cases must not import routes",
        **{k: f"use cases {v}" for k, v in _HTTP_RULES.items()},
    },
    "routes": {
        "infra": "routes must not import concrete infrastructure",
        "server": "routes must not import server composition",
        **{k: f"routes {v}" for k, v in _HTTP_RULES.items()},
    },
    "shared": {
        "infra": "shared code must not import infrastructure",
        "server": "shared code must not import server composition",
        "context": "shared code must not import the app context",
        "schemas": "shared code must not depend on features",
        "ports": "shared code must not depend on features",
        "contracts": "shared code must not depend on features",
        "routes": "shared code must not depend on features",
        "use_cases": "shared code must not depend on features",
        **{k: f"shared code {v}" for k, v in _HTTP_RULES.items()},
    },
    "infra": {
        "server": "infrastructure must not import server composition",
        "context": "infrastructure must not import the app context",
        "use_cases": "infrastructure implements ports; it must not import use cases",
        "routes": "infrastructure must not import routes",
        "contracts": "infrastructure must not import contracts",
    },
    "server": {},
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One doctor problem, anchored to a file (and line, when known)."""

    path: str
    line: int
    message: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}  {self.message}"


def run_doctor(root: Path) -> list[Finding]:
    """Check the application at ``root`` and return all findings."""
    findings = _structure_findings(root)

    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root)
        source_category = _classify_module(_module_parts(relative))
        if source_category is None or source_category == "tests":
            continue
        rules = _RULES.get(source_category)
        if not rules:
            continue
        findings.extend(_import_findings(root, relative, rules))

    return findings


def _structure_findings(root: Path) -> list[Finding]:
    return [
        Finding(rel, 0, "missing (expected by the prescribed structure)")
        for rel in _STRUCTURE
        if not (root / rel).is_file()
    ]


def _import_findings(
    root: Path, relative: Path, rules: dict[Category, str]
) -> list[Finding]:
    try:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [
            Finding(
                relative.as_posix(),
                exc.lineno or 0,
                f"could not parse: {exc.msg}",
            )
        ]

    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for line, target in _imports(tree, relative):
        category = _classify_module(target)
        if category is None:
            continue
        reason = rules.get(category)
        if reason is None:
            continue
        dotted = ".".join(target)
        if (line, dotted) in seen:
            continue
        seen.add((line, dotted))
        findings.append(
            Finding(relative.as_posix(), line, f"imports {dotted}: {reason}")
        )
    return findings


def _imports(tree: ast.Module, relative: Path) -> list[tuple[int, tuple[str, ...]]]:
    """Resolve every import in the module to absolute dotted parts."""
    parts = _module_parts(relative)
    is_package = relative.name == "__init__.py"
    containing = parts if is_package else parts[:-1]

    resolved: list[tuple[int, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved.append((node.lineno, tuple(alias.name.split("."))))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base: tuple[str, ...] = ()
            else:
                base = containing[: len(containing) - (node.level - 1)]
            module = tuple(node.module.split(".")) if node.module else ()
            if _classify_module(base + module) is not None:
                resolved.append((node.lineno, base + module))
                continue
            # `from package import submodule` targets a module the bare
            # package path does not reveal; check each name instead.
            for alias in node.names:
                resolved.append((node.lineno, base + module + (alias.name,)))
    return resolved


def _module_parts(relative: Path) -> tuple[str, ...]:
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _classify_module(parts: tuple[str, ...]) -> Category | None:
    if not parts:
        return None
    if parts[0] != "app":
        if parts[0] in ("starlette", "uvicorn"):
            return "http_runtime"
        if parts[0] == "tenchi" and parts[1:2] in (("server",), ("client",)):
            return "tenchi_runtime"
        return None
    if parts[1:2] == ("infra",):
        return "infra"
    if parts[1:3] == ("server", "context"):
        return "context"
    if parts[1:2] == ("server",):
        return "server"
    if parts[1:2] == ("shared",):
        return "shared"
    if parts[1:2] == ("features",) and len(parts) >= 4:
        return _FEATURE_KINDS.get(parts[3])
    return None
