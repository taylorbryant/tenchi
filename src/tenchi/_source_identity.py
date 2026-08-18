"""Content identity for the Git-visible application source tree."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile
from typing import Annotated, BinaryIO, Protocol

from pydantic import Field
from typing_extensions import TypedDict

from ._git import git_command, git_environment

_GIT_POLL_SECONDS = 0.05

type SourceTreeDigest = Annotated[
    str,
    Field(pattern=r"^sha256:[0-9a-f]{64}$"),
]
type GitCommit = Annotated[
    str,
    Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]


class VerificationSourcePayload(TypedDict):
    head_commit: GitCommit
    tree_digest: SourceTreeDigest
    dirty: bool


@dataclass(frozen=True, slots=True)
class VerificationSource:
    """The immutable identity recorded for one verified worktree state."""

    head_commit: GitCommit
    tree_digest: SourceTreeDigest
    dirty: bool

    def as_dict(self) -> VerificationSourcePayload:
        return {
            "head_commit": self.head_commit,
            "tree_digest": self.tree_digest,
            "dirty": self.dirty,
        }


class SourceIdentityError(RuntimeError):
    """Raised when Tenchi cannot safely identify the current source tree."""


class _Digest(Protocol):
    def update(self, value: bytes, /) -> None: ...


def source_identity(
    root: Path,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> VerificationSource:
    """Fingerprint tracked and nonignored untracked paths below *root*."""
    resolved_root = root.resolve()
    initial_head = _git_output(
        resolved_root,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        action="resolve the current Git commit",
        checkpoint=checkpoint,
    ).strip()
    initial_paths = _git_paths(resolved_root, checkpoint=checkpoint)
    initial_status = _git_status(resolved_root, checkpoint=checkpoint)
    initial_index_state = _git_index_state(resolved_root, checkpoint=checkpoint)

    digest = sha256()
    _add_field(digest, b"tenchi-source-tree-v1")
    for relative_bytes in initial_paths:
        if checkpoint is not None:
            checkpoint()
        _hash_path(
            digest,
            resolved_root,
            relative_bytes,
            checkpoint=checkpoint,
        )

    if checkpoint is not None:
        checkpoint()
    final_head = _git_output(
        resolved_root,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        action="recheck the current Git commit",
        checkpoint=checkpoint,
    ).strip()
    final_paths = _git_paths(resolved_root, checkpoint=checkpoint)
    final_status = _git_status(resolved_root, checkpoint=checkpoint)
    final_index_state = _git_index_state(resolved_root, checkpoint=checkpoint)
    if (
        final_head != initial_head
        or final_paths != initial_paths
        or final_status != initial_status
        or final_index_state != initial_index_state
    ):
        raise SourceIdentityError(
            "source changed while its identity was being captured; rerun verify "
            "against the finished tree"
        )

    head_commit = _decode_head_commit(initial_head)
    return VerificationSource(
        head_commit=head_commit,
        tree_digest=f"sha256:{digest.hexdigest()}",
        dirty=bool(initial_status)
        or _index_can_hide_worktree_changes(initial_index_state),
    )


def _git_paths(
    root: Path,
    *,
    checkpoint: Callable[[], None] | None,
) -> tuple[bytes, ...]:
    output = _git_output(
        root,
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."),
        action="list Git-visible source paths",
        checkpoint=checkpoint,
    )
    return tuple(sorted({path for path in output.split(b"\0") if path}))


def _git_status(
    root: Path,
    *,
    checkpoint: Callable[[], None] | None,
) -> bytes:
    return _git_output(
        root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."),
        action="inspect the source worktree",
        checkpoint=checkpoint,
    )


def _git_index_state(
    root: Path,
    *,
    checkpoint: Callable[[], None] | None,
) -> bytes:
    return _git_output(
        root,
        ("ls-files", "--cached", "-v", "-z", "--", "."),
        action="inspect Git index visibility",
        checkpoint=checkpoint,
    )


def _index_can_hide_worktree_changes(index_state: bytes) -> bool:
    return any(record[:1] != b"H" for record in index_state.split(b"\0") if record)


def _git_output(
    root: Path,
    arguments: Sequence[str],
    *,
    action: str,
    checkpoint: Callable[[], None] | None,
) -> bytes:
    process: subprocess.Popen[bytes] | None = None
    try:
        with (
            TemporaryFile(mode="w+b") as stdout_file,
            TemporaryFile(mode="w+b") as stderr_file,
        ):
            if checkpoint is not None:
                checkpoint()
            process = _start_git_process(
                git_command(*arguments),
                root=root,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
            while process.poll() is None:
                if checkpoint is not None:
                    checkpoint()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_GIT_POLL_SECONDS)
            return_code = process.wait()
            stdout_file.seek(0)
            stdout = stdout_file.read()
            stderr_file.seek(0)
            stderr = stderr_file.read()
    except FileNotFoundError as exc:
        raise SourceIdentityError(
            "could not run git; install Git to identify the verified source"
        ) from exc
    except OSError as exc:
        raise SourceIdentityError(f"could not {action}: {exc}") from exc
    finally:
        if process is not None and process.poll() is None:
            _stop_git_process(process)
    if return_code != 0:
        reason = stderr.decode("utf-8", errors="replace").strip()
        raise SourceIdentityError(
            f"could not {action}: {reason or 'Git command failed'}"
        )
    return stdout


def _start_git_process(
    command: Sequence[str],
    *,
    root: Path,
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=root,
        stdout=stdout_file,
        stderr=stderr_file,
        env=git_environment(),
        start_new_session=os.name == "posix",
    )


def _stop_git_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=1)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.returncode is None:
            process.wait()
        return

    process.terminate()  # pragma: no cover - exercised on Windows CI
    try:  # pragma: no cover - exercised on Windows CI
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:  # pragma: no cover - exercised on Windows CI
        process.kill()
        process.wait()


def _hash_path(
    digest: _Digest,
    root: Path,
    relative_bytes: bytes,
    *,
    checkpoint: Callable[[], None] | None,
) -> None:
    relative = Path(os.fsdecode(relative_bytes))
    if relative.is_absolute() or ".." in relative.parts:
        raise SourceIdentityError("Git returned a source path outside the app root")
    path = root / relative
    _add_field(digest, relative_bytes)
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        _add_field(digest, b"missing")
        return
    except OSError as exc:
        raise SourceIdentityError(
            f"could not inspect Git-visible path {relative.as_posix()!r}: {exc}"
        ) from exc

    if stat.S_ISREG(path_stat.st_mode):
        _add_field(digest, b"file")
        _add_field(digest, _executable_mode(path_stat.st_mode))
        _hash_file(
            digest,
            path,
            relative,
            path_stat,
            checkpoint=checkpoint,
        )
        return
    if stat.S_ISLNK(path_stat.st_mode):
        _add_field(digest, b"symlink")
        try:
            target = os.readlink(path)
            final_stat = path.lstat()
        except OSError as exc:
            raise SourceIdentityError(
                f"could not inspect Git-visible symlink {relative.as_posix()!r}: {exc}"
            ) from exc
        if not _same_file_state(path_stat, final_stat):
            raise SourceIdentityError(
                "source changed while its identity was being captured; rerun verify "
                "against the finished tree"
            )
        _add_field(digest, os.fsencode(target))
        return
    if stat.S_ISDIR(path_stat.st_mode):
        nested_root = _nested_git_root(path, checkpoint=checkpoint)
        if nested_root == path.resolve():
            nested = source_identity(path, checkpoint=checkpoint)
            _add_field(digest, b"git-worktree")
            _add_field(digest, nested.head_commit.encode("ascii"))
            _add_field(digest, nested.tree_digest.encode("ascii"))
            _add_field(digest, b"dirty" if nested.dirty else b"clean")
        else:
            _add_field(digest, b"directory")
        try:
            final_stat = path.lstat()
        except OSError as exc:
            raise SourceIdentityError(
                f"could not recheck Git-visible path {relative.as_posix()!r}: {exc}"
            ) from exc
        if not _same_file_state(path_stat, final_stat):
            raise SourceIdentityError(
                "source changed while its identity was being captured; rerun verify "
                "against the finished tree"
            )
        return
    raise SourceIdentityError(
        f"Git-visible path {relative.as_posix()!r} has an unsupported file type"
    )


def _hash_file(
    digest: _Digest,
    path: Path,
    relative: Path,
    path_stat: os.stat_result,
    *,
    checkpoint: Callable[[], None] | None,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceIdentityError(
            f"could not read Git-visible file {relative.as_posix()!r}: {exc}"
        ) from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or not _same_file(
            path_stat, opened_stat
        ):
            raise SourceIdentityError(
                "source changed while its identity was being captured; rerun verify "
                "against the finished tree"
            )
        _add_field(digest, str(opened_stat.st_size).encode("ascii"))
        while True:
            if checkpoint is not None:
                checkpoint()
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        final_stat = os.fstat(descriptor)
        if not _same_file_state(opened_stat, final_stat):
            raise SourceIdentityError(
                "source changed while its identity was being captured; rerun verify "
                "against the finished tree"
            )
        try:
            final_path_stat = path.lstat()
        except OSError as exc:
            raise SourceIdentityError(
                f"could not recheck Git-visible file {relative.as_posix()!r}: {exc}"
            ) from exc
        if not _same_file_state(opened_stat, final_path_stat):
            raise SourceIdentityError(
                "source changed while its identity was being captured; rerun verify "
                "against the finished tree"
            )
    except OSError as exc:
        raise SourceIdentityError(
            f"could not read Git-visible file {relative.as_posix()!r}: {exc}"
        ) from exc
    finally:
        os.close(descriptor)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _nested_git_root(
    path: Path,
    *,
    checkpoint: Callable[[], None] | None,
) -> Path | None:
    if not os.path.lexists(path / ".git"):
        return None
    output = _git_output(
        path,
        ("rev-parse", "--show-toplevel"),
        action="inspect a nested Git worktree",
        checkpoint=checkpoint,
    )
    try:
        return Path(os.fsdecode(output.strip())).resolve()
    except OSError as exc:
        raise SourceIdentityError("could not resolve a nested Git worktree") from exc


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return _same_file(left, right) and (
        _mode_identity(left.st_mode),
        left.st_size,
        left.st_mtime_ns,
    ) == (
        _mode_identity(right.st_mode),
        right.st_size,
        right.st_mtime_ns,
    )


def _mode_identity(mode: int) -> int:
    return stat.S_IFMT(mode) | (mode & stat.S_IXUSR)


def _executable_mode(mode: int) -> bytes:
    return b"executable" if mode & stat.S_IXUSR else b"non-executable"


def _add_field(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _decode_head_commit(value: bytes) -> str:
    try:
        commit = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SourceIdentityError(
            "the current Git commit could not be decoded"
        ) from exc
    if len(commit) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise SourceIdentityError("the current Git commit is not a valid object id")
    return commit


__all__ = [
    "SourceIdentityError",
    "VerificationSource",
    "VerificationSourcePayload",
    "source_identity",
]
