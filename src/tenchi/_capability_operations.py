"""Previewable, create-only adoption of optional application capabilities."""

from __future__ import annotations

from pathlib import Path
from stat import S_ISDIR

from ._cli_operations import (
    _write_files_transactionally,  # pyright: ignore[reportPrivateUsage]
)
from ._cli_results import MakeResult
from .scaffold import capability_files


def make_capability_result(root: Path, *, name: str, dry_run: bool) -> MakeResult:
    """Plan or create tools composition and evidence without importing the app."""
    root = root.resolve()

    def failure(message: str) -> MakeResult:
        return MakeResult(
            root=str(root),
            artifact="capability",
            name=name,
            feature=None,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=f"tenchi make capability: {message}",
        )

    if name != "tools":
        return failure(f"unsupported capability {name!r}; supported: tools")

    files = capability_files(name)
    # A package also defines app.server.tools. Refuse every conflict before
    # writing anything, including dangling links and links in parent paths.
    conflicts: set[str] = set()
    try:
        for relative in (*files, "app/server/tools"):
            destination = root / relative
            if _path_mode(destination) is not None:
                conflicts.add(relative)
            for parent in destination.parents:
                if parent == root:
                    break
                mode = _path_mode(parent)
                if mode is not None and not S_ISDIR(mode):
                    conflicts.add(parent.relative_to(root).as_posix())
        server_mode = _path_mode(root / "app/server")
    except OSError as exc:
        return failure(f"could not inspect application paths: {exc}")
    if conflicts:
        return failure(
            "refusing existing or unsafe paths: "
            + ", ".join(sorted(conflicts))
            + "; no files were created. Integrate tools manually: https://tenchi.io/tools"
        )
    if server_mode is None or not S_ISDIR(server_mode):
        return failure("app/server/ not found; run this from an application root")

    if not dry_run:
        write_error = _write_files_transactionally(root, files)
        if write_error is not None:
            return failure(f"could not create files: {write_error}")

    return MakeResult(
        root=str(root),
        artifact="capability",
        name=name,
        feature=None,
        dry_run=dry_run,
        ok=True,
        files=tuple(files),
        next_steps=(
            "In tenchi.toml, add or set tools = true inside [verify], preserving "
            "all other stages. If there is no policy file, the built-in policy "
            "already requires tools while app/server/tools.py exists.",
            "For a new feature, select tools explicitly: "
            "tenchi make feature <name> --with tools. For an existing feature, "
            "declare its tool() contracts and tool_handler() bindings in "
            "app/features/<name>/tools.py.",
            "In app/server/tools.py, import the feature group: "
            "from app.features.<name>.tools import tools as feature_tools; "
            "then set tools = tool_group(feature_tools).",
            "Supply authenticated identity through app-owned context wiring "
            "when invoking tools; keep authorization in the use cases. "
            "See https://tenchi.io/tools for execution and transport setup.",
            "Review the manifest changes: tenchi tools --diff tools.json; "
            "then accept them: tenchi tools --write tools.json.",
            "Run tenchi check, then tenchi verify --base-ref <ref> using the "
            "commit before adoption. A baseline without app/server/tools.py "
            "or app/server/tools/__init__.py records tools as a first adoption.",
        ),
    )


def _path_mode(path: Path) -> int | None:
    """Distinguish missing paths from errors consistently on Python 3.12+."""
    try:
        return path.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
