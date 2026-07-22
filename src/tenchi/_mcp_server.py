"""Optional MCP stdio adapter over Tenchi's renderer-independent operations."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ResourceError, ToolError
from mcp.server.session import ServerSession
from mcp.types import Annotations, ToolAnnotations
from pydantic import Field

from . import __version__
from ._app_map import (
    AppMapNodeKind,
    AppMapPayload,
    map_app,
    project_app_map,
)
from ._checks import CheckCancelled, run_check
from ._cli_operations import (
    doctor_result,
    make_feature_result,
    make_use_case_result,
    openapi_defaults,
    routes_result,
)
from ._cli_results import (
    CheckPayload,
    CheckStepResult,
    DoctorPayload,
    GeneratedArtifact,
    MakePayload,
    RoutesPayload,
)
from ._openapi_operations import (
    OpenApiDiffPayload,
    OperationError,
    isolated_project_imports,
    load_route_group,
    openapi_diff_result,
    project_path,
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_CHECK_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


@dataclass(frozen=True, slots=True)
class McpServerOptions:
    """Fixed application boundary captured by one MCP server process."""

    root: Path
    routes: str = "app.server.routes:routes"
    api_routes: str = "app.server.routes:api_routes"
    snapshot: str = "openapi.json"
    title: str | None = None
    version: str | None = None
    description: str | None = None
    security_json: str | None = None


def build_mcp_server(options: McpServerOptions) -> FastMCP[None]:
    """Build a Tenchi MCP server suitable for in-memory or stdio use."""
    root = options.root.resolve()
    if not (root / "app").is_dir():
        raise OperationError(
            f"app/ not found under {root}; choose a Tenchi application root"
        )
    project_path(root, options.snapshot)
    operation_lock = asyncio.Lock()
    progress_tasks: set[asyncio.Task[None]] = set()
    module_names = tuple(
        target.partition(":")[0] for target in (options.routes, options.api_routes)
    )

    def finish_progress(task: asyncio.Task[None]) -> None:
        progress_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    server: FastMCP[None] = FastMCP(
        "Tenchi",
        instructions=(
            "Inspect the project instructions first, map the affected feature, "
            "preview generated structure before editing, validate with check, "
            "and compare OpenAPI before accepting a changed snapshot. Tenchi MCP "
            "inspection and preview tools do not write application files; check "
            "runs project-owned validation commands."
        ),
        website_url="https://tenchi.io/mcp",
    )
    server._mcp_server.version = __version__  # pyright: ignore[reportPrivateUsage]

    async def call[T](operation: Callable[[], T]) -> T:
        async with operation_lock:
            try:
                return await asyncio.to_thread(
                    _isolated_call,
                    root,
                    module_names,
                    operation,
                )
            except OperationError as exc:
                raise ToolError(str(exc)) from exc

    @server.tool(
        name="app_map",
        description=(
            "Return Tenchi's deterministic, source-backed application graph. "
            "Project by feature or node kind to keep agent context focused."
        ),
        annotations=_READ_ONLY,
    )
    async def app_map(  # pyright: ignore[reportUnusedFunction]
        feature: str | None = None,
        kinds: list[AppMapNodeKind] | None = None,
    ) -> AppMapPayload:
        def operation() -> AppMapPayload:
            group = load_route_group(root, options.api_routes)
            result = map_app(root, group)
            if feature is not None:
                available = sorted(
                    node.name for node in result.nodes if node.kind == "feature"
                )
                if feature not in available:
                    choices = ", ".join(available) if available else "none"
                    raise OperationError(
                        f"unknown feature {feature!r}; available features: {choices}"
                    )
            return project_app_map(result, feature=feature, kinds=kinds).as_dict()

        return await call(operation)

    @server.tool(
        name="routes",
        description=(
            "Return the versioned composed HTTP route table, including response "
            "statuses, errors, public access, metadata, limits, and use cases."
        ),
        annotations=_READ_ONLY,
    )
    async def routes() -> RoutesPayload:  # pyright: ignore[reportUnusedFunction]
        return await call(
            lambda: routes_result(
                root, load_route_group(root, options.routes)
            ).as_dict()
        )

    @server.tool(
        name="doctor",
        description=(
            "Check Tenchi structure, dependency direction, wiring, and "
            "authorization consistency without changing files."
        ),
        annotations=_READ_ONLY,
    )
    async def doctor() -> DoctorPayload:  # pyright: ignore[reportUnusedFunction]
        return await call(lambda: doctor_result(root).as_dict())

    @server.tool(
        name="openapi_diff",
        description=(
            "Compare generated OpenAPI with a project snapshot or the same "
            "snapshot at a Git ref. Breaking and unknown changes are incompatible."
        ),
        annotations=_READ_ONLY,
    )
    async def openapi_diff(  # pyright: ignore[reportUnusedFunction]
        snapshot: str | None = None,
        ref: str | None = None,
    ) -> OpenApiDiffPayload:
        selected = options.snapshot if snapshot is None else snapshot

        def operation() -> OpenApiDiffPayload:
            project_path(root, selected)
            return openapi_diff_result(
                root,
                routes=options.api_routes,
                snapshot=Path(selected),
                ref=ref,
                title=options.title,
                version=options.version,
                description=options.description,
                security_json=options.security_json,
            ).as_dict()

        return await call(operation)

    @server.tool(
        name="make_preview",
        description=(
            "Preview a Tenchi feature or use-case generator. The result performs "
            "normal validation and lists files and wiring steps but never writes."
        ),
        annotations=_READ_ONLY,
    )
    async def make_preview(  # pyright: ignore[reportUnusedFunction]
        artifact: GeneratedArtifact,
        name: str,
        feature: str | None = None,
    ) -> MakePayload:
        if artifact == "feature":
            if feature is not None:
                raise ToolError("feature must be omitted when artifact is 'feature'")
            return await call(
                lambda: make_feature_result(root, name=name, dry_run=True).as_dict()
            )
        if feature is None:
            raise ToolError("feature is required when artifact is 'use-case'")
        return await call(
            lambda: make_use_case_result(
                root, feature=feature, name=name, dry_run=True
            ).as_dict()
        )

    @server.tool(
        name="check",
        description=(
            "Run Ruff format, Ruff lint, Pyright, pytest, doctor, and the OpenAPI "
            "snapshot check. Project-owned commands run with their normal side "
            "effects; output is bounded and cancellation stops the active process."
        ),
        annotations=_CHECK_ANNOTATIONS,
    )
    async def check(  # pyright: ignore[reportUnusedFunction]
        ctx: Context[ServerSession, None],
        timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 600.0,
    ) -> CheckPayload:
        async with operation_lock:
            try:
                snapshot_path = project_path(root, options.snapshot)
            except OperationError as exc:
                raise ToolError(str(exc)) from exc
            title, version, description, security_json = openapi_defaults(
                root,
                routes=options.api_routes,
                title=options.title,
                version=options.version,
                description=options.description,
                security_json=options.security_json,
            )
            cancelled = Event()
            loop = asyncio.get_running_loop()

            def progress(index: int, total: int, step: CheckStepResult) -> None:
                def schedule() -> None:
                    task = asyncio.create_task(
                        ctx.report_progress(
                            progress=float(index),
                            total=float(total),
                            message=(
                                f"Completed {step.name} ({index}/{total}): "
                                f"{step.status}"
                            ),
                        )
                    )
                    progress_tasks.add(task)
                    task.add_done_callback(finish_progress)

                try:
                    loop.call_soon_threadsafe(schedule)
                except RuntimeError:
                    # A disconnected client may close the loop while the
                    # validation worker is finishing its active step.
                    return

            def operation() -> CheckPayload:
                return _redirected_call(
                    lambda: run_check(
                        root,
                        routes=options.api_routes,
                        title=title,
                        version=version,
                        description=description,
                        snapshot=str(snapshot_path),
                        security_json=security_json,
                        timeout_seconds=timeout_seconds,
                        cancelled=cancelled.is_set,
                        step_completed=progress,
                    ).as_dict()
                )

            worker = asyncio.create_task(asyncio.to_thread(operation))
            try:
                result = await asyncio.shield(worker)
                if progress_tasks:
                    await asyncio.gather(*tuple(progress_tasks), return_exceptions=True)
                return result
            except asyncio.CancelledError:
                cancelled.set()
                with suppress(CheckCancelled):
                    await asyncio.shield(worker)
                raise
            except CheckCancelled as exc:  # defensive: cancellation owns this path
                raise ToolError("check was cancelled") from exc

    @server.resource(
        "tenchi://project/agents",
        name="Tenchi project instructions",
        description=(
            "Repository-local placement rules and the recommended Tenchi agent loop."
        ),
        mime_type="text/markdown",
        annotations=Annotations(audience=["assistant"], priority=1.0),
    )
    def project_agents() -> str:  # pyright: ignore[reportUnusedFunction]
        path = root / "AGENTS.md"
        try:
            resolved_path = path.resolve()
            resolved_path.relative_to(root)
            return resolved_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _fallback_agent_instructions()
        except ValueError as exc:
            raise ResourceError(
                "AGENTS.md must stay inside the application root"
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise ResourceError(f"could not read AGENTS.md: {exc}") from exc

    return server


def run_mcp_server(options: McpServerOptions) -> None:
    """Run a Tenchi MCP server over stdio until its client disconnects."""
    root = options.root.resolve()
    server = build_mcp_server(
        McpServerOptions(
            root=root,
            routes=options.routes,
            api_routes=options.api_routes,
            snapshot=options.snapshot,
            title=options.title,
            version=options.version,
            description=options.description,
            security_json=options.security_json,
        )
    )
    os.chdir(root)
    server.run(transport="stdio")


def _redirected_call[T](operation: Callable[[], T]) -> T:
    with redirect_stdout(sys.stderr):
        return operation()


def _isolated_call[T](
    root: Path, module_names: tuple[str, ...], operation: Callable[[], T]
) -> T:
    with isolated_project_imports(root, module_names=module_names):
        return _redirected_call(operation)


def _fallback_agent_instructions() -> str:
    return """# Tenchi project workflow

1. Run `app_map` for the affected feature and inspect diagnostics and unresolved
   relationships before editing.
2. Use `make_preview` before creating framework-shaped files.
3. Keep contracts at the boundary, behavior in async use cases, infrastructure
   behind protocols, and wiring explicit in the server composition root.
4. Run `check` after a coherent change.
5. Run `openapi_diff` before accepting a changed OpenAPI snapshot.

Full guidance: https://tenchi.io/agents
"""
