"""Build a deterministic, source-backed graph of a Tenchi application."""

from __future__ import annotations

import ast
import inspect
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from ._cli_results import DiagnosticResult
from .contracts import Contract
from .doctor import run_doctor
from .routes import Route, RouteGroup

type AppMapNodeKind = Literal[
    "feature",
    "contract",
    "route",
    "use-case",
    "policy",
    "port",
    "adapter",
    "context",
    "entrypoint",
    "test",
]
type AppMapNodeStatus = Literal["declared", "registered"]
type AppMapEdgeKind = Literal[
    "owns",
    "binds",
    "depends-on",
    "implements",
    "authorizes",
    "contains-test",
]
type AppMapConfidence = Literal["exact", "inferred"]
type AppMapDetailValue = (
    str | int | float | bool | None | tuple[str, ...] | tuple[int, ...]
)

app_map_node_kinds: tuple[AppMapNodeKind, ...] = (
    "feature",
    "contract",
    "route",
    "use-case",
    "policy",
    "port",
    "adapter",
    "context",
    "entrypoint",
    "test",
)


@dataclass(frozen=True, slots=True)
class AppMapSource:
    """Project-relative source evidence for a graph item."""

    path: str
    line: int | None = None
    symbol: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "symbol": self.symbol}


@dataclass(frozen=True, slots=True)
class AppMapNode:
    """One application concept discovered from structure or route binding."""

    id: str
    kind: AppMapNodeKind
    name: str
    source: AppMapSource
    status: AppMapNodeStatus
    feature: str | None = None
    details: tuple[tuple[str, AppMapDetailValue], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "feature": self.feature,
            "source": self.source.as_dict(),
            "status": self.status,
            "details": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.details
            },
        }


@dataclass(frozen=True, slots=True)
class AppMapEdge:
    """One directed relationship with the source that proves it."""

    kind: AppMapEdgeKind
    source: str
    target: str
    evidence: AppMapSource
    confidence: AppMapConfidence

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "evidence": self.evidence.as_dict(),
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class AppMapUnresolvedReference:
    """A relationship the analyzer could not resolve into graph nodes."""

    code: str
    message: str
    source: AppMapSource

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class AppMapSummary:
    """Aggregate counts for a complete or projected application graph."""

    features: int
    contracts: int
    routes: int
    use_cases: int
    policies: int
    ports: int
    adapters: int
    contexts: int
    entrypoints: int
    tests: int
    diagnostics: int
    unresolved: int

    def as_dict(self) -> dict[str, object]:
        return {
            "features": self.features,
            "contracts": self.contracts,
            "routes": self.routes,
            "use_cases": self.use_cases,
            "policies": self.policies,
            "ports": self.ports,
            "adapters": self.adapters,
            "contexts": self.contexts,
            "entrypoints": self.entrypoints,
            "tests": self.tests,
            "diagnostics": self.diagnostics,
            "unresolved": self.unresolved,
        }


@dataclass(frozen=True, slots=True)
class AppMapResult:
    """Version 1 source-backed graph for one Tenchi application."""

    root: str
    summary: AppMapSummary
    nodes: tuple[AppMapNode, ...]
    edges: tuple[AppMapEdge, ...]
    diagnostics: tuple[DiagnosticResult, ...]
    unresolved: tuple[AppMapUnresolvedReference, ...]
    schema_version: Literal[1] = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "summary": self.summary.as_dict(),
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "unresolved": [item.as_dict() for item in self.unresolved],
        }


@dataclass(frozen=True, slots=True)
class _SymbolRef:
    module: str
    symbol: str | None


@dataclass(frozen=True, slots=True)
class _ModuleInfo:
    module: str
    path: Path
    relative: str
    feature: str | None
    tree: ast.Module
    imports: Mapping[str, _SymbolRef]


@dataclass(frozen=True, slots=True)
class _ContractRecord:
    node_id: str
    module: str
    symbol: str
    method: str | None
    path: str | None
    contract_name: str | None


@dataclass(frozen=True, slots=True)
class _RouteBinding:
    contract: _SymbolRef | None
    use_case: _SymbolRef | None
    source: AppMapSource


@dataclass(frozen=True, slots=True)
class _ImportBinding:
    reference: _SymbolRef
    line: int


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, AppMapNode] = {}
        self.symbol_nodes: dict[tuple[str, str], str] = {}
        self.edges: dict[tuple[str, str, str], AppMapEdge] = {}
        self.unresolved: list[AppMapUnresolvedReference] = []

    def add_node(
        self, node: AppMapNode, *, module: str | None = None, symbol: str | None = None
    ) -> None:
        existing = self.nodes.get(node.id)
        if existing is not None and existing.source != node.source:
            self.unresolved.append(
                AppMapUnresolvedReference(
                    code="TENCHI_MAP_DUPLICATE_NODE_ID",
                    message=f"multiple declarations resolve to {node.id}",
                    source=node.source,
                )
            )
            return
        self.nodes[node.id] = node
        if module is not None and symbol is not None:
            self.symbol_nodes[(module, symbol)] = node.id

    def register(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        if node is not None and node.status != "registered":
            self.nodes[node_id] = replace(node, status="registered")

    def update_details(
        self,
        node_id: str,
        details: tuple[tuple[str, AppMapDetailValue], ...],
    ) -> None:
        node = self.nodes.get(node_id)
        if node is not None:
            self.nodes[node_id] = replace(node, details=details)

    def add_edge(self, edge: AppMapEdge) -> None:
        key = (edge.kind, edge.source, edge.target)
        existing = self.edges.get(key)
        if existing is None or _edge_evidence_rank(edge) < _edge_evidence_rank(
            existing
        ):
            self.edges[key] = edge

    def add_unresolved(self, code: str, message: str, source: AppMapSource) -> None:
        self.unresolved.append(
            AppMapUnresolvedReference(code=code, message=message, source=source)
        )


def map_app(root: Path, routes: RouteGroup) -> AppMapResult:
    """Inspect *root* and combine its source graph with exact route bindings."""
    resolved_root = root.resolve()
    builder = _GraphBuilder()
    modules = _read_modules(resolved_root, builder)
    contracts: dict[str, _ContractRecord] = {}

    _discover_features(resolved_root, builder)
    _discover_source_nodes(modules, builder, contracts)
    _discover_adapters(modules, builder)
    _register_wired_adapters(modules, builder)
    _add_ownership_edges(builder)
    _add_import_edges(modules, builder)
    context_fields = _add_context_port_edges(modules, builder)
    _add_use_case_port_edges(modules, builder, context_fields)
    bindings = _route_bindings(modules)
    _add_runtime_routes(routes, builder, contracts, bindings, resolved_root)

    diagnostics = tuple(
        sorted(
            (
                DiagnosticResult(
                    code=finding.code,
                    severity="error",
                    message=finding.message,
                    path=finding.path,
                    line=finding.line or None,
                )
                for finding in run_doctor(resolved_root)
            ),
            key=lambda item: (item.path, item.line or 0, item.code, item.message),
        )
    )
    nodes = tuple(sorted(builder.nodes.values(), key=lambda node: (node.kind, node.id)))
    edges = tuple(
        sorted(
            builder.edges.values(),
            key=lambda edge: (
                edge.kind,
                edge.source,
                edge.target,
                edge.evidence.path,
                edge.evidence.line or 0,
            ),
        )
    )
    unresolved = tuple(
        sorted(
            builder.unresolved,
            key=lambda item: (
                item.source.path,
                item.source.line or 0,
                item.code,
                item.message,
            ),
        )
    )
    return AppMapResult(
        root=str(resolved_root),
        summary=_summary(nodes, diagnostics, unresolved),
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
        unresolved=unresolved,
    )


def project_app_map(
    result: AppMapResult,
    *,
    feature: str | None = None,
    kinds: Sequence[AppMapNodeKind] | None = None,
) -> AppMapResult:
    """Return a deterministic feature and/or node-kind projection."""
    selected_ids = {node.id for node in result.nodes}
    if feature is not None:
        selected_ids = {
            node.id
            for node in result.nodes
            if node.feature == feature
            or (node.kind == "feature" and node.name == feature)
        }
        directly_related = set(selected_ids)
        for edge in result.edges:
            if edge.source in selected_ids or edge.target in selected_ids:
                directly_related.add(edge.source)
                directly_related.add(edge.target)
        selected_ids = directly_related
    if kinds is not None:
        selected_kinds = set(kinds)
        selected_ids = {
            node.id
            for node in result.nodes
            if node.id in selected_ids and node.kind in selected_kinds
        }

    nodes = tuple(node for node in result.nodes if node.id in selected_ids)
    edges = tuple(
        edge
        for edge in result.edges
        if edge.source in selected_ids and edge.target in selected_ids
    )
    if feature is None:
        diagnostics = result.diagnostics
        unresolved = result.unresolved
    else:
        prefix = f"app/features/{feature}/"
        selected_source_paths = {node.source.path for node in nodes}
        diagnostics = tuple(
            item
            for item in result.diagnostics
            if item.path.startswith(prefix) or item.path in selected_source_paths
        )
        unresolved = tuple(
            item
            for item in result.unresolved
            if item.source.path.startswith(prefix)
            or item.source.path in selected_source_paths
        )
    return AppMapResult(
        root=result.root,
        summary=_summary(nodes, diagnostics, unresolved),
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
        unresolved=unresolved,
    )


def format_app_map(result: AppMapResult) -> str:
    """Render a concise terminal view of an application graph."""
    summary = result.summary
    lines = [
        "Tenchi app map",
        f"Root: {result.root}",
        (
            "Summary: "
            f"{summary.features} features, {summary.contracts} contracts, "
            f"{summary.routes} routes, {summary.use_cases} use cases, "
            f"{summary.ports} ports, {summary.adapters} adapters, "
            f"{len(result.edges)} relationships"
        ),
    ]
    grouped: dict[AppMapNodeKind, list[AppMapNode]] = defaultdict(list)
    for node in result.nodes:
        grouped[node.kind].append(node)
    for kind in app_map_node_kinds:
        nodes = grouped.get(kind)
        if not nodes:
            continue
        lines.extend(("", f"{kind}:"))
        for node in nodes:
            location = _format_source(node.source)
            feature = f" [{node.feature}]" if node.feature is not None else ""
            lines.append(
                f"  {node.name}{feature} ({node.status})"
                f"{f' — {location}' if location else ''}"
            )
    if result.edges:
        lines.extend(("", "relationships:"))
        for edge in result.edges:
            lines.append(
                f"  {edge.source} --{edge.kind}--> {edge.target} "
                f"[{edge.confidence}] — {_format_source(edge.evidence)}"
            )
    if result.diagnostics:
        lines.extend(("", "Diagnostics:"))
        lines.extend(
            f"  [{item.code}] {item.message} ({item.path}"
            f"{f':{item.line}' if item.line is not None else ''})"
            for item in result.diagnostics
        )
    if result.unresolved:
        lines.extend(("", "Unresolved:"))
        lines.extend(
            f"  [{item.code}] {item.message} ({_format_source(item.source)})"
            for item in result.unresolved
        )
    return "\n".join(lines)


def _read_modules(root: Path, builder: _GraphBuilder) -> tuple[_ModuleInfo, ...]:
    app_files = sorted((root / "app").rglob("*.py"))
    root_tests = sorted((root / "tests").rglob("test_*.py"))
    modules: list[_ModuleInfo] = []
    for path in dict.fromkeys((*app_files, *root_tests)):
        relative_path = path.relative_to(root)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            builder.add_unresolved(
                "TENCHI_MAP_SOURCE_READ_ERROR",
                f"could not read source: {exc}",
                AppMapSource(path=relative_path.as_posix()),
            )
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            builder.add_unresolved(
                "TENCHI_MAP_SOURCE_PARSE_ERROR",
                f"could not parse source: {exc.msg}",
                AppMapSource(path=relative_path.as_posix(), line=exc.lineno),
            )
            continue
        module = _module_name(relative_path)
        modules.append(
            _ModuleInfo(
                module=module,
                path=path,
                relative=relative_path.as_posix(),
                feature=_feature_for_path(relative_path),
                tree=tree,
                imports=_imported_symbols(tree, module, path.name == "__init__.py"),
            )
        )
    return tuple(modules)


def _discover_features(root: Path, builder: _GraphBuilder) -> None:
    features_root = root / "app" / "features"
    if not features_root.is_dir():
        return
    for path in sorted(
        item
        for item in features_root.iterdir()
        if item.is_dir()
        and not item.name.startswith((".", "_"))
        and (item / "__init__.py").is_file()
    ):
        relative = path.relative_to(root).as_posix()
        builder.add_node(
            AppMapNode(
                id=_feature_id(path.name),
                kind="feature",
                name=path.name,
                source=AppMapSource(path=relative),
                status="declared",
                feature=path.name,
            )
        )


def _discover_source_nodes(
    modules: Sequence[_ModuleInfo],
    builder: _GraphBuilder,
    contracts: dict[str, _ContractRecord],
) -> None:
    for info in modules:
        if _is_test_path(info.relative):
            node = AppMapNode(
                id=f"test:{info.relative}",
                kind="test",
                name=Path(info.relative).name,
                source=AppMapSource(path=info.relative, line=1),
                status="declared",
                feature=info.feature,
            )
            builder.add_node(node, module=info.module, symbol="__module__")
            continue
        if info.relative.endswith("/contracts.py"):
            _discover_contracts(info, builder, contracts)
        if "/use_cases/" in info.relative:
            _discover_functions(info, builder, kind="use-case")
        if info.relative.endswith("/policy.py"):
            _discover_functions(info, builder, kind="policy")
        _discover_ports(info, builder)
        if info.relative == "app/server/context.py":
            _discover_contexts(info, builder)
        if info.relative == "app/server/asgi.py" or _has_main_guard(info.tree):
            builder.add_node(
                AppMapNode(
                    id=f"entrypoint:{info.module}",
                    kind="entrypoint",
                    name=info.module,
                    source=AppMapSource(path=info.relative, line=1),
                    status="declared",
                ),
                module=info.module,
                symbol="__module__",
            )


def _discover_contracts(
    info: _ModuleInfo,
    builder: _GraphBuilder,
    contracts: dict[str, _ContractRecord],
) -> None:
    for statement in info.tree.body:
        symbol, call = _assigned_call(statement)
        if symbol is None or call is None or _terminal_name(call.func) != "contract":
            continue
        method = _literal_keyword(call, "method", str)
        path = _literal_keyword(call, "path", str)
        explicit_name = _literal_keyword(call, "name", str)
        contract_name = explicit_name or (
            f"{method} {path}" if method is not None and path is not None else None
        )
        node_id = _symbol_id("contract", info.feature, info.module, symbol)
        status_value = _keyword_value(call, "status")
        responses_value = _keyword_value(call, "responses")
        status = _literal_keyword(call, "status", int)
        public_value = _keyword_value(call, "public")
        public = _literal_keyword(call, "public", bool)
        tags_value = _keyword_value(call, "tags")
        tags = _literal_string_tuple(call, "tags")
        details = _details(
            export_name=symbol,
            method=method,
            path=path,
            statuses=(
                None
                if responses_value is not None
                else (status,)
                if status is not None
                else (200,)
                if status_value is None
                else None
            ),
            public=False if public_value is None else public,
            tags=() if tags_value is None else tags,
        )
        builder.add_node(
            AppMapNode(
                id=node_id,
                kind="contract",
                name=contract_name or symbol,
                source=AppMapSource(
                    path=info.relative, line=statement.lineno, symbol=symbol
                ),
                status="declared",
                feature=info.feature,
                details=details,
            ),
            module=info.module,
            symbol=symbol,
        )
        contracts[node_id] = _ContractRecord(
            node_id=node_id,
            module=info.module,
            symbol=symbol,
            method=method,
            path=path,
            contract_name=contract_name,
        )


def _discover_functions(
    info: _ModuleInfo,
    builder: _GraphBuilder,
    *,
    kind: Literal["use-case", "policy"],
) -> None:
    for statement in info.tree.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if kind == "use-case":
            if not isinstance(statement, ast.AsyncFunctionDef):
                continue
            if _is_async_generator(statement):
                continue
        if statement.name.startswith("_"):
            continue
        builder.add_node(
            AppMapNode(
                id=_symbol_id(kind, info.feature, info.module, statement.name),
                kind=kind,
                name=statement.name,
                source=AppMapSource(
                    path=info.relative, line=statement.lineno, symbol=statement.name
                ),
                status="declared",
                feature=info.feature,
            ),
            module=info.module,
            symbol=statement.name,
        )


def _discover_ports(info: _ModuleInfo, builder: _GraphBuilder) -> None:
    for statement in info.tree.body:
        if not isinstance(statement, ast.ClassDef) or statement.name.startswith("_"):
            continue
        if not any(_terminal_name(base) == "Protocol" for base in statement.bases):
            continue
        builder.add_node(
            AppMapNode(
                id=_symbol_id("port", info.feature, info.module, statement.name),
                kind="port",
                name=statement.name,
                source=AppMapSource(
                    path=info.relative, line=statement.lineno, symbol=statement.name
                ),
                status="declared",
                feature=info.feature,
            ),
            module=info.module,
            symbol=statement.name,
        )


def _discover_contexts(info: _ModuleInfo, builder: _GraphBuilder) -> None:
    for statement in info.tree.body:
        if not isinstance(statement, ast.ClassDef) or statement.name.startswith("_"):
            continue
        builder.add_node(
            AppMapNode(
                id=f"context:{statement.name}",
                kind="context",
                name=statement.name,
                source=AppMapSource(
                    path=info.relative, line=statement.lineno, symbol=statement.name
                ),
                status="declared",
            ),
            module=info.module,
            symbol=statement.name,
        )


def _discover_adapters(modules: Sequence[_ModuleInfo], builder: _GraphBuilder) -> None:
    ports = [node for node in builder.nodes.values() if node.kind == "port"]
    for info in modules:
        if not info.relative.startswith("app/infra/") or _is_test_path(info.relative):
            continue
        for statement in info.tree.body:
            if not isinstance(statement, ast.ClassDef) or statement.name.startswith(
                "_"
            ):
                continue
            candidates = [port for port in ports if statement.name.endswith(port.name)]
            if not candidates:
                continue
            node_id = _symbol_id("adapter", None, info.module, statement.name)
            builder.add_node(
                AppMapNode(
                    id=node_id,
                    kind="adapter",
                    name=statement.name,
                    source=AppMapSource(
                        path=info.relative,
                        line=statement.lineno,
                        symbol=statement.name,
                    ),
                    status="declared",
                ),
                module=info.module,
                symbol=statement.name,
            )
            if len(candidates) == 1:
                builder.add_edge(
                    AppMapEdge(
                        kind="implements",
                        source=node_id,
                        target=candidates[0].id,
                        evidence=AppMapSource(
                            path=info.relative,
                            line=statement.lineno,
                            symbol=statement.name,
                        ),
                        confidence="inferred",
                    )
                )
            else:
                builder.add_unresolved(
                    "TENCHI_MAP_AMBIGUOUS_PORT_IMPLEMENTATION",
                    f"{statement.name} matches multiple ports named "
                    f"{candidates[0].name}",
                    AppMapSource(
                        path=info.relative,
                        line=statement.lineno,
                        symbol=statement.name,
                    ),
                )


def _register_wired_adapters(
    modules: Sequence[_ModuleInfo], builder: _GraphBuilder
) -> None:
    """Mark only adapters reached by executable entrypoint wiring."""
    functions = {
        (info.module, statement.name): (info, statement)
        for info in modules
        for statement in info.tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    adapters = {
        (module, symbol): node_id
        for (module, symbol), node_id in builder.symbol_nodes.items()
        if builder.nodes[node_id].kind == "adapter"
    }
    entrypoints = {
        node.source.path: node
        for node in builder.nodes.values()
        if node.kind == "entrypoint"
    }

    for relative, entrypoint in entrypoints.items():
        info = next((item for item in modules if item.relative == relative), None)
        if info is None:
            continue
        pending = list(_called_functions(info, info.tree, functions))
        visited: set[tuple[str, str]] = set()
        reached: dict[str, AppMapSource] = _called_adapters(info, info.tree, adapters)
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            definition = functions.get(key)
            if definition is None:
                continue
            function_info, function = definition
            reached.update(_called_adapters(function_info, function, adapters))
            pending.extend(
                referenced
                for referenced in _called_functions(
                    function_info,
                    function,
                    functions,
                )
                if referenced not in visited
            )

        for adapter_id, evidence in sorted(reached.items()):
            builder.register(adapter_id)
            builder.add_edge(
                AppMapEdge(
                    kind="depends-on",
                    source=entrypoint.id,
                    target=adapter_id,
                    evidence=evidence,
                    confidence="exact",
                )
            )


def _add_ownership_edges(builder: _GraphBuilder) -> None:
    for node in tuple(builder.nodes.values()):
        if node.feature is None or node.kind == "feature":
            continue
        feature_id = _feature_id(node.feature)
        if feature_id not in builder.nodes:
            continue
        builder.add_edge(
            AppMapEdge(
                kind="contains-test" if node.kind == "test" else "owns",
                source=feature_id,
                target=node.id,
                evidence=node.source,
                confidence="exact",
            )
        )


def _add_import_edges(modules: Sequence[_ModuleInfo], builder: _GraphBuilder) -> None:
    nodes_by_path: dict[str, list[AppMapNode]] = defaultdict(list)
    for node in builder.nodes.values():
        nodes_by_path[node.source.path].append(node)
    for info in modules:
        origins = nodes_by_path.get(info.relative, [])
        if not origins:
            continue
        for origin in origins:
            scope = _definition_scope(info.tree, origin)
            if scope is None:
                continue
            for reference, line in _used_import_references(info, scope):
                if reference.symbol is None:
                    continue
                target_id = builder.symbol_nodes.get(
                    (reference.module, reference.symbol)
                )
                if target_id is None:
                    continue
                target = builder.nodes[target_id]
                if origin.id == target_id or origin.kind in ("feature", "route"):
                    continue
                if origin.kind == "context" and target.kind == "port":
                    continue
                kind: AppMapEdgeKind = (
                    "authorizes"
                    if origin.kind == "use-case" and target.kind == "policy"
                    else "depends-on"
                )
                builder.add_edge(
                    AppMapEdge(
                        kind=kind,
                        source=origin.id,
                        target=target_id,
                        evidence=AppMapSource(
                            path=info.relative,
                            line=line,
                        ),
                        confidence="exact",
                    )
                )
                if origin.kind == "entrypoint" and target.kind == "use-case":
                    builder.register(target_id)


def _definition_scope(tree: ast.Module, node: AppMapNode) -> ast.AST | None:
    if node.kind in ("entrypoint", "test"):
        return tree
    for statement in tree.body:
        if getattr(statement, "lineno", None) != node.source.line:
            continue
        if (
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and statement.name == node.source.symbol
        ):
            return statement
        symbol, _ = _assigned_call(statement)
        if symbol == node.source.symbol:
            return statement
    return None


def _add_context_port_edges(
    modules: Sequence[_ModuleInfo], builder: _GraphBuilder
) -> dict[str, str]:
    fields: dict[str, str] = {}
    info = next(
        (item for item in modules if item.relative == "app/server/context.py"), None
    )
    if info is None:
        return fields
    for statement in info.tree.body:
        if not isinstance(statement, ast.ClassDef):
            continue
        context_id = builder.symbol_nodes.get((info.module, statement.name))
        if context_id is None:
            continue
        for member in statement.body:
            if not isinstance(member, ast.AnnAssign) or not isinstance(
                member.target, ast.Name
            ):
                continue
            for name in _annotation_names(member.annotation):
                reference = info.imports.get(name)
                if reference is None or reference.symbol is None:
                    continue
                port_id = builder.symbol_nodes.get((reference.module, reference.symbol))
                if port_id is None or builder.nodes[port_id].kind != "port":
                    continue
                existing = fields.get(member.target.id)
                if existing is not None and existing != port_id:
                    builder.add_unresolved(
                        "TENCHI_MAP_AMBIGUOUS_CONTEXT_PORT",
                        (
                            f"context field {member.target.id!r} resolves to "
                            "multiple ports"
                        ),
                        AppMapSource(path=info.relative, line=member.lineno),
                    )
                    continue
                fields[member.target.id] = port_id
                builder.add_edge(
                    AppMapEdge(
                        kind="depends-on",
                        source=context_id,
                        target=port_id,
                        evidence=AppMapSource(path=info.relative, line=member.lineno),
                        confidence="exact",
                    )
                )
    return fields


def _add_use_case_port_edges(
    modules: Sequence[_ModuleInfo],
    builder: _GraphBuilder,
    context_fields: Mapping[str, str],
) -> None:
    if not context_fields:
        return
    for info in modules:
        if "/use_cases/" not in info.relative:
            continue
        function_nodes = {
            node.source.symbol: node
            for node in builder.nodes.values()
            if node.kind == "use-case" and node.source.path == info.relative
        }
        for statement in info.tree.body:
            if not isinstance(statement, ast.AsyncFunctionDef):
                continue
            origin = function_nodes.get(statement.name)
            if origin is None:
                continue
            for node in ast.walk(statement):
                if not (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "context"
                ):
                    continue
                port_id = context_fields.get(node.attr)
                if port_id is None:
                    continue
                builder.add_edge(
                    AppMapEdge(
                        kind="depends-on",
                        source=origin.id,
                        target=port_id,
                        evidence=AppMapSource(path=info.relative, line=node.lineno),
                        confidence="exact",
                    )
                )


def _route_bindings(modules: Sequence[_ModuleInfo]) -> tuple[_RouteBinding, ...]:
    bindings: list[_RouteBinding] = []
    for info in modules:
        if not info.relative.endswith("/routes.py"):
            continue
        for node in ast.walk(info.tree):
            if not (
                isinstance(node, ast.Call)
                and _terminal_name(node.func) == "route"
                and len(node.args) >= 2
            ):
                continue
            bindings.append(
                _RouteBinding(
                    contract=_expression_reference(node.args[0], info),
                    use_case=_expression_reference(node.args[1], info),
                    source=AppMapSource(path=info.relative, line=node.lineno),
                )
            )
    return tuple(bindings)


def _add_runtime_routes(
    routes: RouteGroup,
    builder: _GraphBuilder,
    contracts: Mapping[str, _ContractRecord],
    bindings: Sequence[_RouteBinding],
    root: Path,
) -> None:
    duplicate_counts: dict[str, int] = defaultdict(int)
    for item in routes:
        use_case_ref = _callable_reference(item)
        use_case_id = (
            builder.symbol_nodes.get((use_case_ref.module, use_case_ref.symbol))
            if use_case_ref is not None and use_case_ref.symbol is not None
            else None
        )
        binding = _matching_binding(item, use_case_ref, bindings, contracts, builder)
        contract_id = _contract_id_for_route(item, binding, contracts, builder)
        source = (
            binding.source
            if binding is not None
            else _route_fallback_source(item, root)
        )
        base_id = f"route:{item.contract.method} {item.contract.path}"
        duplicate_counts[base_id] += 1
        route_id = (
            base_id
            if duplicate_counts[base_id] == 1
            else f"{base_id}#{duplicate_counts[base_id]}"
        )
        feature = _node_feature(contract_id, builder) or _node_feature(
            use_case_id, builder
        )
        statuses = _contract_statuses(item.contract)
        builder.add_node(
            AppMapNode(
                id=route_id,
                kind="route",
                name=f"{item.contract.method} {item.contract.path}",
                source=source,
                status="registered",
                feature=feature,
                details=_details(
                    method=item.contract.method,
                    path=item.contract.path,
                    contract_name=item.contract.name,
                    statuses=statuses,
                    public=item.contract.public,
                    call_kwargs=item.call_kwargs,
                ),
            )
        )
        if feature is not None:
            builder.add_edge(
                AppMapEdge(
                    kind="owns",
                    source=_feature_id(feature),
                    target=route_id,
                    evidence=source,
                    confidence="exact",
                )
            )
        if contract_id is None:
            builder.add_unresolved(
                "TENCHI_MAP_CONTRACT_SOURCE_UNRESOLVED",
                f"could not locate the declaration for {item.contract.name}",
                source,
            )
        else:
            record = contracts.get(contract_id)
            if record is not None:
                declared_contract = _declared_contract(record, item.contract)
                builder.update_details(
                    contract_id,
                    _details(
                        export_name=record.symbol,
                        method=declared_contract.method,
                        path=declared_contract.path,
                        statuses=_contract_statuses(declared_contract),
                        public=declared_contract.public,
                        tags=declared_contract.tags,
                    ),
                )
            builder.register(contract_id)
            builder.add_edge(
                AppMapEdge(
                    kind="binds",
                    source=route_id,
                    target=contract_id,
                    evidence=source,
                    confidence="exact" if binding is not None else "inferred",
                )
            )
        if use_case_id is None:
            builder.add_unresolved(
                "TENCHI_MAP_USE_CASE_SOURCE_UNRESOLVED",
                f"could not locate the use case for {item.contract.name}",
                source,
            )
        else:
            builder.register(use_case_id)
            builder.add_edge(
                AppMapEdge(
                    kind="binds",
                    source=route_id,
                    target=use_case_id,
                    evidence=source,
                    confidence="exact",
                )
            )


def _matching_binding(
    route: Route,
    use_case: _SymbolRef | None,
    bindings: Sequence[_RouteBinding],
    contracts: Mapping[str, _ContractRecord],
    builder: _GraphBuilder,
) -> _RouteBinding | None:
    if use_case is None:
        return None
    candidates = [binding for binding in bindings if binding.use_case == use_case]
    matching: list[_RouteBinding] = []
    for binding in candidates:
        if binding.contract is None or binding.contract.symbol is None:
            continue
        node_id = builder.symbol_nodes.get(
            (binding.contract.module, binding.contract.symbol)
        )
        record = contracts.get(node_id or "")
        if record is not None and _contract_record_matches(record, route.contract):
            matching.append(binding)
    if len(matching) == 1:
        return matching[0]
    return candidates[0] if len(candidates) == 1 else None


def _contract_id_for_route(
    route: Route,
    binding: _RouteBinding | None,
    contracts: Mapping[str, _ContractRecord],
    builder: _GraphBuilder,
) -> str | None:
    if binding is not None and binding.contract is not None:
        reference = binding.contract
        if reference.symbol is not None:
            node_id = builder.symbol_nodes.get((reference.module, reference.symbol))
            if node_id is not None:
                return node_id
    identity_matches: list[str] = []
    for record in contracts.values():
        module = sys.modules.get(record.module)
        if (
            module is not None
            and getattr(module, record.symbol, None) is route.contract
        ):
            identity_matches.append(record.node_id)
    if len(identity_matches) == 1:
        return identity_matches[0]
    matches = [
        record.node_id
        for record in contracts.values()
        if _contract_record_matches(record, route.contract)
    ]
    return matches[0] if len(matches) == 1 else None


def _contract_record_matches(
    record: _ContractRecord, contract: Contract[object, object]
) -> bool:
    if record.method is not None and record.method != contract.method:
        return False
    if record.contract_name is not None and record.contract_name == contract.name:
        return True
    return record.path is not None and contract.path.endswith(record.path)


def _declared_contract(
    record: _ContractRecord,
    fallback: Contract[object, object],
) -> Contract[object, object]:
    module = sys.modules.get(record.module)
    candidate = getattr(module, record.symbol, None) if module is not None else None
    if isinstance(candidate, Contract):
        return cast(Contract[object, object], candidate)
    return fallback


def _contract_statuses(contract: Contract[object, object]) -> tuple[int, ...]:
    return tuple(definition.status for definition in contract.responses) or (
        contract.status,
    )


def _callable_reference(route: Route) -> _SymbolRef | None:
    value = inspect.unwrap(route.use_case)
    module = getattr(value, "__module__", None)
    name = getattr(value, "__name__", None)
    if isinstance(module, str) and isinstance(name, str):
        return _SymbolRef(module=module, symbol=name)
    return None


def _route_fallback_source(route: Route, root: Path) -> AppMapSource:
    value = inspect.unwrap(route.use_case)
    try:
        path = inspect.getsourcefile(value)
        _, line = inspect.getsourcelines(value)
    except (OSError, TypeError):
        return AppMapSource(path="<runtime>")
    if path is None:
        return AppMapSource(path="<runtime>", line=line)
    try:
        relative = Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        relative = path
    return AppMapSource(
        path=relative, line=line, symbol=getattr(value, "__name__", None)
    )


def _add_summary_count(kind: AppMapNodeKind, counts: dict[AppMapNodeKind, int]) -> None:
    counts[kind] = counts.get(kind, 0) + 1


def _edge_evidence_rank(edge: AppMapEdge) -> tuple[int, str, int]:
    return (
        0 if edge.confidence == "exact" else 1,
        edge.evidence.path,
        edge.evidence.line if edge.evidence.line is not None else sys.maxsize,
    )


def _summary(
    nodes: Sequence[AppMapNode],
    diagnostics: Sequence[DiagnosticResult],
    unresolved: Sequence[AppMapUnresolvedReference],
) -> AppMapSummary:
    counts: dict[AppMapNodeKind, int] = {}
    for node in nodes:
        _add_summary_count(node.kind, counts)
    return AppMapSummary(
        features=counts.get("feature", 0),
        contracts=counts.get("contract", 0),
        routes=counts.get("route", 0),
        use_cases=counts.get("use-case", 0),
        policies=counts.get("policy", 0),
        ports=counts.get("port", 0),
        adapters=counts.get("adapter", 0),
        contexts=counts.get("context", 0),
        entrypoints=counts.get("entrypoint", 0),
        tests=counts.get("test", 0),
        diagnostics=len(diagnostics),
        unresolved=len(unresolved),
    )


def _details(**values: AppMapDetailValue) -> tuple[tuple[str, AppMapDetailValue], ...]:
    return tuple(
        sorted((key, value) for key, value in values.items() if value is not None)
    )


def _symbol_id(
    kind: AppMapNodeKind, feature: str | None, module: str, symbol: str
) -> str:
    owner = f"{feature}." if feature is not None else f"{module}."
    return f"{kind}:{owner}{symbol}"


def _feature_id(feature: str) -> str:
    return f"feature:{feature}"


def _node_feature(node_id: str | None, builder: _GraphBuilder) -> str | None:
    if node_id is None:
        return None
    node = builder.nodes.get(node_id)
    return node.feature if node is not None else None


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _feature_for_path(relative: Path) -> str | None:
    parts = relative.parts
    if len(parts) >= 3 and parts[:2] == ("app", "features"):
        return parts[2]
    return None


def _is_test_path(relative: str) -> bool:
    path = Path(relative)
    return path.name.startswith("test_")


def _has_main_guard(tree: ast.Module) -> bool:
    for statement in tree.body:
        if not isinstance(statement, ast.If) or not isinstance(
            statement.test, ast.Compare
        ):
            continue
        comparison = statement.test
        if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
            continue
        values = (comparison.left, *comparison.comparators)
        has_name = any(
            isinstance(value, ast.Name) and value.id == "__name__" for value in values
        )
        has_main = any(
            isinstance(value, ast.Constant) and value.value == "__main__"
            for value in values
        )
        if has_name and has_main:
            return True
    return False


def _assigned_call(statement: ast.stmt) -> tuple[str | None, ast.Call | None]:
    target: ast.expr | None = None
    value: ast.expr | None = None
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        value = statement.value
    elif isinstance(statement, ast.AnnAssign):
        target = statement.target
        value = statement.value
    if isinstance(target, ast.Name) and isinstance(value, ast.Call):
        return target.id, value
    return None, None


def _literal_keyword[ValueT: (str, int, bool)](
    call: ast.Call, name: str, expected: type[ValueT]
) -> ValueT | None:
    value = next((item.value for item in call.keywords if item.arg == name), None)
    if value is None:
        return None
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    return literal if type(literal) is expected else None


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _literal_string_tuple(call: ast.Call, name: str) -> tuple[str, ...] | None:
    value = next((item.value for item in call.keywords if item.arg == name), None)
    if value is None:
        return None
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if isinstance(literal, (tuple, list)):
        items = cast(Sequence[object], literal)
        if all(isinstance(item, str) for item in items):
            return tuple(cast(str, item) for item in items)
    return None


def _terminal_name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Subscript):
        return _terminal_name(value.value)
    return None


class _YieldVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_Yield(self, node: ast.Yield) -> None:
        self.found = True

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.found = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _is_async_generator(function: ast.AsyncFunctionDef) -> bool:
    visitor = _YieldVisitor()
    for statement in function.body:
        visitor.visit(statement)
    return visitor.found


class _ScopeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _scope_nodes(
    scope: ast.AST, *, include_signature: bool = False
) -> tuple[ast.AST, ...]:
    """Return nodes executed by one module or function, excluding nested scopes."""
    visitor = _ScopeVisitor()
    if include_signature and isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        visitor.visit(scope.args)
        if scope.returns is not None:
            visitor.visit(scope.returns)
        for decorator in scope.decorator_list:
            visitor.visit(decorator)
    body = getattr(scope, "body", ())
    if isinstance(body, list):
        for statement in cast(list[ast.stmt], body):
            visitor.visit(statement)
    return tuple(visitor.nodes)


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, module: str, is_package: bool) -> None:
        self.bindings: dict[str, _ImportBinding] = {}
        self._package = module.split(".") if is_package else module.split(".")[:-1]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname is None:
                local_name = alias.name.split(".")[0]
                target_module = local_name
            else:
                local_name = alias.asname
                target_module = alias.name
            self.bindings[local_name] = _ImportBinding(
                reference=_SymbolRef(module=target_module, symbol=None),
                line=node.lineno,
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            keep = max(0, len(self._package) - (node.level - 1))
            base = self._package[:keep]
        else:
            base = []
        if node.module:
            base.extend(node.module.split("."))
        target_module = ".".join(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            self.bindings[alias.asname or alias.name] = _ImportBinding(
                reference=_SymbolRef(module=target_module, symbol=alias.name),
                line=node.lineno,
            )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _collect_imports(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    is_package: bool,
) -> dict[str, _ImportBinding]:
    visitor = _ImportVisitor(module, is_package)
    for statement in scope.body:
        visitor.visit(statement)
    return visitor.bindings


def _imported_symbols(
    tree: ast.Module, module: str, is_package: bool
) -> dict[str, _SymbolRef]:
    return {
        name: binding.reference
        for name, binding in _collect_imports(tree, module, is_package).items()
    }


def _imports_for_scope(info: _ModuleInfo, scope: ast.AST) -> dict[str, _ImportBinding]:
    bindings = _collect_imports(
        info.tree,
        info.module,
        info.path.name == "__init__.py",
    )
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bindings.update(
            _collect_imports(
                scope,
                info.module,
                info.path.name == "__init__.py",
            )
        )
    return bindings


def _attribute_chain(value: ast.expr) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


def _reference_from_expression(
    value: ast.expr,
    *,
    module: str,
    imports: Mapping[str, _ImportBinding],
) -> _SymbolRef | None:
    chain = _attribute_chain(value)
    if chain is None:
        return None
    binding = imports.get(chain[0])
    if len(chain) == 1:
        return (
            binding.reference
            if binding is not None
            else _SymbolRef(module=module, symbol=chain[0])
        )
    if binding is None:
        return None
    target_parts = [binding.reference.module]
    if binding.reference.symbol is not None:
        target_parts.append(binding.reference.symbol)
    target_parts.extend(chain[1:-1])
    return _SymbolRef(module=".".join(target_parts), symbol=chain[-1])


def _expression_reference(value: ast.expr, info: _ModuleInfo) -> _SymbolRef | None:
    imports = {
        name: _ImportBinding(reference=reference, line=0)
        for name, reference in info.imports.items()
    }
    return _reference_from_expression(value, module=info.module, imports=imports)


def _used_import_references(
    info: _ModuleInfo, scope: ast.AST
) -> tuple[tuple[_SymbolRef, int], ...]:
    imports = _imports_for_scope(info, scope)
    references: dict[tuple[str, str | None], int] = {}
    for node in _scope_nodes(scope, include_signature=True):
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
            getattr(node, "ctx", None), ast.Load
        ):
            continue
        chain = _attribute_chain(node)
        if chain is None or chain[0] not in imports:
            continue
        reference = _reference_from_expression(
            node,
            module=info.module,
            imports=imports,
        )
        if reference is None:
            continue
        key = (reference.module, reference.symbol)
        references[key] = min(references.get(key, sys.maxsize), imports[chain[0]].line)
    return tuple(
        (_SymbolRef(module=module, symbol=symbol), line)
        for (module, symbol), line in sorted(
            references.items(),
            key=lambda item: (item[0][0], item[0][1] or "", item[1]),
        )
    )


def _called_functions(
    info: _ModuleInfo,
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
    functions: Mapping[
        tuple[str, str],
        tuple[_ModuleInfo, ast.FunctionDef | ast.AsyncFunctionDef],
    ],
) -> tuple[tuple[str, str], ...]:
    imports = _imports_for_scope(info, scope)
    references: set[tuple[str, str]] = set()
    for node in _scope_nodes(scope):
        if not isinstance(node, ast.Call):
            continue
        expressions = (
            node.func,
            *node.args,
            *(keyword.value for keyword in node.keywords),
        )
        for expression in expressions:
            for candidate in ast.walk(expression):
                if not isinstance(candidate, (ast.Name, ast.Attribute)):
                    continue
                reference = _reference_from_expression(
                    candidate,
                    module=info.module,
                    imports=imports,
                )
                if reference is None or reference.symbol is None:
                    continue
                key = (reference.module, reference.symbol)
                if key in functions:
                    references.add(key)
    return tuple(sorted(references))


def _called_adapters(
    info: _ModuleInfo,
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
    adapters: Mapping[tuple[str, str], str],
) -> dict[str, AppMapSource]:
    imports = _imports_for_scope(info, scope)
    reached: dict[str, AppMapSource] = {}
    for node in _scope_nodes(scope):
        if not isinstance(node, ast.Call):
            continue
        reference = _reference_from_expression(
            node.func,
            module=info.module,
            imports=imports,
        )
        if reference is None or reference.symbol is None:
            continue
        adapter_id = adapters.get((reference.module, reference.symbol))
        if adapter_id is None:
            continue
        evidence = AppMapSource(path=info.relative, line=node.lineno)
        existing = reached.get(adapter_id)
        if existing is None or (evidence.line or 0) < (existing.line or 0):
            reached[adapter_id] = evidence
    return reached


def _annotation_names(annotation: ast.expr) -> set[str]:
    return {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}


def _format_source(source: AppMapSource) -> str:
    if source.line is not None:
        return f"{source.path}:{source.line}"
    return source.path
