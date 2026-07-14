"""Architecture checks for ``tenchi doctor``.

Doctor enforces the dependency direction that keeps Tenchi apps honest:

- Domain and schema code must not import infrastructure or the HTTP runtime.
- Use cases may import schemas, ports, policies, the app context, and
  shared errors — never concrete infrastructure or server composition.
- Policies take their subjects as arguments: they import schemas, domain
  types, and shared errors, and nothing with I/O behind it.
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
import io
import tokenize
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
    "policy": "policy",
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
    "policy": {
        "infra": "policies must not import infrastructure",
        "server": "policies must not import server composition",
        "context": "policies must not import the app context; they take "
        "their subjects as arguments",
        "use_cases": "policies must not import use cases",
        "routes": "policies must not import routes",
        "contracts": "policies must not import contracts",
        **{k: f"policies {v}" for k, v in _HTTP_RULES.items()},
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
        "policy": "shared code must not depend on features",
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
        parts = _module_parts(relative)
        source_category = _classify_module(parts)
        if source_category == "tests":
            continue

        findings.extend(_placement_findings(relative, parts, source_category))

        # Every non-test module is parsed, whatever its category — a file
        # doctor has no rules for can still hide a syntax error.
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            findings.append(
                Finding(
                    relative.as_posix(),
                    exc.lineno or 0,
                    f"could not parse: {exc.msg}",
                )
            )
            continue

        rules = _RULES.get(source_category) if source_category else None
        if rules:
            findings.extend(_import_findings(tree, relative, rules))

    findings.extend(_authorization_findings(root))
    return findings


def _placement_findings(
    relative: Path, parts: tuple[str, ...], category: Category | None
) -> list[Finding]:
    """Flag feature modules the prescribed structure has no place for —
    otherwise a stray ``helpers.py`` (or a nested feature tree) would
    silently escape every dependency rule."""
    if parts[1:2] != ("features",) or category is not None:
        return []
    if len(parts) <= 2 or (relative.name == "__init__.py" and len(parts) == 3):
        return []
    return [
        Finding(
            relative.as_posix(),
            0,
            "unrecognized feature module: features contain schemas.py, "
            "ports.py, contracts.py, routes.py, policy.py, use_cases/, "
            "and tests/ only",
        )
    ]


_PUBLIC_PRAGMA = "# doctor: public"


def _authorization_findings(root: Path) -> list[Finding]:
    """Flag use cases that skip authorization in an app that uses it.

    This is a consistency check, not a proof: if any use case references
    authorization (``require_user``, ``context.user``, or a policy
    import), every use case must do the same or carry the explicit
    ``# doctor: public`` pragma. Apps with no authorization anywhere are
    left alone.
    """
    surveyed: list[tuple[Path, bool, bool]] = []
    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root)
        if relative.name == "__init__.py":
            continue
        if _classify_module(_module_parts(relative)) != "use_cases":
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # already reported by the parse pass
        guarded = _references_authorization(tree, relative)
        surveyed.append((relative, guarded, _has_public_pragma(source)))

    if not any(guarded for _, guarded, _ in surveyed):
        return []

    return [
        Finding(
            relative.as_posix(),
            0,
            "use case makes no authorization reference while other use "
            "cases in this app do; call require_user or a policy, read "
            f"context.user, or mark deliberate exposure with {_PUBLIC_PRAGMA!r}",
        )
        for relative, guarded, has_pragma in surveyed
        if not guarded and not has_pragma
    ]


def _has_public_pragma(source: str) -> bool:
    """True when ``# doctor: public`` appears as an actual comment.

    A substring match would also fire inside docstrings and string
    literals — text that merely *mentions* the pragma must not exempt a
    use case from the authorization check.
    """
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return any(
            token.type == tokenize.COMMENT and "doctor: public" in token.string
            for token in tokens
        )
    except (tokenize.TokenError, SyntaxError):
        return False


def _references_authorization(tree: ast.Module, relative: Path) -> bool:
    for _, target in _imports(tree, relative):
        if _classify_module(target) == "policy":
            return True
        if target and target[-1] == "require_user":
            return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "require_user":
                return True
            # Only context.user counts — todo.user on a domain object is
            # data access, not an authorization guard.
            if (
                node.attr == "user"
                and isinstance(node.value, ast.Name)
                and node.value.id == "context"
            ):
                return True
        if isinstance(node, ast.Name) and node.id == "require_user":
            return True
    return False


def _structure_findings(root: Path) -> list[Finding]:
    return [
        Finding(rel, 0, "missing (expected by the prescribed structure)")
        for rel in _STRUCTURE
        if not (root / rel).is_file()
    ]


def _import_findings(
    tree: ast.Module, relative: Path, rules: dict[Category, str]
) -> list[Finding]:
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
            # Resolve per imported name first: `from app.server import
            # context` depends on app.server.context (allowed in use
            # cases), not on server composition at large. Fall back to
            # the module path when the name itself classifies nowhere.
            for alias in node.names:
                candidate = base + module + (alias.name,)
                if _classify_module(candidate) is not None:
                    resolved.append((node.lineno, candidate))
                else:
                    resolved.append((node.lineno, base + module))
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
        if parts[0] == "tenchi" and parts[1:2] in (
            ("server",),
            ("client",),
            ("execution",),
        ):
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
