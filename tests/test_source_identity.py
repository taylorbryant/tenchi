import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from tenchi import _source_identity
from tenchi._source_identity import SourceIdentityError, source_identity


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet")
    (tmp_path / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=Tenchi Tests",
        "-c",
        "user.email=tests@tenchi.invalid",
        "commit",
        "--quiet",
        "-m",
        "initial",
    )
    return tmp_path


def test_source_identity_is_stable_and_bound_to_head(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    first = source_identity(root)
    second = source_identity(root)

    assert first == second
    assert first.head_commit == _git(root, "rev-parse", "HEAD")
    assert first.tree_digest.startswith("sha256:")
    assert len(first.tree_digest) == 71
    assert first.dirty is False


def test_source_identity_rejects_an_invalid_head_object_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_git_output(
        root: Path,
        arguments: Sequence[str],
        *,
        action: str,
        checkpoint: object,
    ) -> bytes:
        del root, action, checkpoint
        return b"not-an-object-id\n" if arguments[0] == "rev-parse" else b""

    monkeypatch.setattr(_source_identity, "_git_output", invalid_git_output)

    with pytest.raises(SourceIdentityError, match="not a valid object id"):
        source_identity(tmp_path)


@pytest.mark.parametrize("change", ["modify", "delete", "untracked"])
def test_source_identity_includes_every_git_visible_state(
    tmp_path: Path,
    change: str,
) -> None:
    root = _repository(tmp_path)
    initial = source_identity(root)

    if change == "modify":
        (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    elif change == "delete":
        (root / "app.py").unlink()
    else:
        (root / "new.py").write_text("new = True\n", encoding="utf-8")

    changed = source_identity(root)

    assert changed.head_commit == initial.head_commit
    assert changed.tree_digest != initial.tree_digest
    assert changed.dirty is True


def test_source_identity_excludes_ignored_paths(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    initial = source_identity(root)

    (root / "runtime.ignored").write_text("first\n", encoding="utf-8")
    ignored = source_identity(root)
    (root / "runtime.ignored").write_text("second\n", encoding="utf-8")

    assert ignored == initial
    assert source_identity(root) == initial


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_source_identity_does_not_report_hidden_index_state_as_clean(
    tmp_path: Path,
    flag: str,
) -> None:
    root = _repository(tmp_path)
    _git(root, "update-index", flag, "app.py")
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")

    identity = source_identity(root)

    assert identity.dirty is True


def test_source_identity_is_scoped_to_the_application_root(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    app = root / "service"
    app.mkdir()
    (app / "main.py").write_text("service = True\n", encoding="utf-8")
    (root / "sibling.py").write_text("sibling = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Tenchi Tests",
        "-c",
        "user.email=tests@tenchi.invalid",
        "commit",
        "--quiet",
        "-m",
        "add service",
    )
    initial = source_identity(app)

    (root / "sibling.py").write_text("sibling = 2\n", encoding="utf-8")

    assert source_identity(app) == initial


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and symlink semantics")
def test_source_identity_includes_modes_and_symlink_targets(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    link = root / "current.py"
    link.symlink_to("app.py")
    _git(root, "add", "current.py")
    _git(
        root,
        "-c",
        "user.name=Tenchi Tests",
        "-c",
        "user.email=tests@tenchi.invalid",
        "commit",
        "--quiet",
        "-m",
        "add link",
    )
    initial = source_identity(root)

    link.unlink()
    link.symlink_to("other.py")
    linked_elsewhere = source_identity(root)
    assert linked_elsewhere.tree_digest != initial.tree_digest

    link.unlink()
    link.symlink_to("app.py")
    (root / "app.py").chmod(0o755)
    assert source_identity(root).tree_digest != initial.tree_digest


def test_source_identity_calls_the_cancellation_checkpoint(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    class Cancelled(Exception):
        pass

    def cancel() -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        source_identity(root, checkpoint=cancel)


def test_source_identity_stops_git_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path)
    processes: list[subprocess.Popen[bytes]] = []

    def start_slow_process(
        command: Sequence[str],
        *,
        root: Path,
        stdout_file: BinaryIO,
        stderr_file: BinaryIO,
    ) -> subprocess.Popen[bytes]:
        del command
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=root,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=os.name == "posix",
        )
        processes.append(process)
        return process

    class Cancelled(Exception):
        pass

    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Cancelled

    monkeypatch.setattr(
        _source_identity,
        "_start_git_process",
        start_slow_process,
    )

    with pytest.raises(Cancelled):
        source_identity(root, checkpoint=cancel)

    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_source_identity_ignores_repository_environment_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "app"
    root.mkdir()
    _repository(root)
    other = tmp_path / "other"
    other.mkdir()
    _repository(other)
    (other / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(other, "add", "app.py")
    _git(
        other,
        "-c",
        "user.name=Tenchi Tests",
        "-c",
        "user.email=tests@tenchi.invalid",
        "commit",
        "--quiet",
        "-m",
        "other source",
    )
    local_commit = _git(root, "rev-parse", "HEAD")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(other))

    identity = source_identity(root)

    assert identity.head_commit == local_commit


def test_source_identity_rejects_a_file_replaced_after_it_was_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path)
    source = root / "app.py"
    source.write_text("value = 2\n", encoding="utf-8")
    source_inode = source.stat().st_ino
    original_read = os.read
    replaced = False

    def replace_after_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = original_read(descriptor, size)
        if chunk and not replaced and os.fstat(descriptor).st_ino == source_inode:
            replacement = root / "replacement.py"
            replacement.write_text("value = 3\n", encoding="utf-8")
            replacement.replace(source)
            replaced = True
        return chunk

    monkeypatch.setattr(os, "read", replace_after_read)

    with pytest.raises(
        SourceIdentityError,
        match="source changed while its identity was being captured",
    ):
        source_identity(root)

    assert replaced is True


def test_source_identity_recursively_identifies_git_submodules(
    tmp_path: Path,
) -> None:
    child = tmp_path / "child"
    child.mkdir()
    _repository(child)
    parent = tmp_path / "parent"
    parent.mkdir()
    _repository(parent)
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(child),
        "dependency",
    )
    _git(parent, "add", ".")
    _git(
        parent,
        "-c",
        "user.name=Tenchi Tests",
        "-c",
        "user.email=tests@tenchi.invalid",
        "commit",
        "--quiet",
        "-m",
        "add dependency",
    )
    initial = source_identity(parent)
    dependency_source = parent / "dependency/app.py"

    dependency_source.write_text("value = 2\n", encoding="utf-8")
    first_dirty_state = source_identity(parent)
    dependency_source.write_text("value = 3\n", encoding="utf-8")
    second_dirty_state = source_identity(parent)

    assert initial.dirty is False
    assert first_dirty_state.dirty is True
    assert second_dirty_state.dirty is True
    assert first_dirty_state.tree_digest != initial.tree_digest
    assert second_dirty_state.tree_digest != first_dirty_state.tree_digest
