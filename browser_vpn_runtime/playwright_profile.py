"""Playwright persistent profile directory operations."""

import ctypes
import os
import shutil
import sys
import tempfile
from pathlib import Path

_AT_FDCWD = -100
_CHROMIUM_SINGLETON_NAME_TUPLE = ("SingletonCookie", "SingletonLock", "SingletonSocket")
_RENAME_EXCHANGE = 2


def _directory_fsync(path: Path) -> None:
    """Persist directory entry changes for one directory.

    Args:
        path: Directory whose entries must be durable.
    """

    file_descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _directory_tree_copy(source_path: Path, target_path: Path) -> None:
    """Copy one directory tree.

    Args:
        source_path: Existing source directory.
        target_path: Destination directory.
    """

    if source_path.is_symlink():
        raise ValueError(f"profile source must be a regular directory: {source_path}")
    shutil.copytree(
        source_path,
        target_path,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*_CHROMIUM_SINGLETON_NAME_TUPLE),
        symlinks=True,
    )
    _directory_tree_validate(target_path)


def _directory_tree_atomic_replace(source_path: Path, target_path: Path) -> None:
    """Publish one prepared directory without removing an existing target name.

    Args:
        source_path: Prepared sibling directory tree.
        target_path: Published target directory tree.

    Raises:
        RuntimeError: If an existing target must be exchanged outside Linux.
    """

    if target_path.is_symlink() or (target_path.exists() and not target_path.is_dir()):
        raise ValueError(f"profile target must be a regular directory: {target_path}")
    if not target_path.exists():
        os.replace(source_path, target_path)
        _directory_fsync(target_path.parent)
        return
    if sys.platform != "linux":
        raise RuntimeError("atomic replacement of an existing profile directory requires Linux renameat2")
    _directory_tree_exchange(source_path, target_path)
    try:
        shutil.rmtree(source_path)
    finally:
        _directory_fsync(target_path.parent)


def _directory_tree_exchange(source_path: Path, target_path: Path) -> None:
    """Exchange two existing sibling directory names atomically on Linux.

    Args:
        source_path: Prepared new directory tree.
        target_path: Currently published directory tree.

    Raises:
        OSError: If Linux cannot perform `renameat2(RENAME_EXCHANGE)`.
        RuntimeError: If libc does not expose the required Linux primitive.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise RuntimeError("Linux libc must expose renameat2 for atomic profile replacement") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source_path),
        _AT_FDCWD,
        os.fsencode(target_path),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        errno_value = ctypes.get_errno()
        raise OSError(errno_value, os.strerror(errno_value), f"{source_path} <-> {target_path}")


def _directory_tree_validate(path: Path) -> None:
    """Require one profile tree to contain only regular directories and files.

    Args:
        path: Profile tree to validate without following links.

    Raises:
        ValueError: If the root or a child is a symlink or another special entry.
    """

    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"profile tree must be a regular directory: {path}")
    pending_directory_path_list = [path]
    while pending_directory_path_list:
        directory_path = pending_directory_path_list.pop()
        with os.scandir(directory_path) as entry_iterator:
            for entry in entry_iterator:
                if entry.is_symlink():
                    raise ValueError(f"profile tree must not contain symlinks: {entry.path}")
                if entry.is_dir(follow_symlinks=False):
                    pending_directory_path_list.append(Path(entry.path))
                elif not entry.is_file(follow_symlinks=False):
                    raise ValueError(f"profile tree must contain only regular entries: {entry.path}")


def _directory_tree_write_enable(path: Path) -> None:
    """Enable owner writes on one runtime profile tree.

    Args:
        path: Runtime profile root copied from an immutable source.
    """

    path.chmod(path.stat().st_mode | 0o700)
    for child_path in path.rglob("*"):
        owner_mode = 0o700 if child_path.is_dir() else 0o600
        child_path.chmod(child_path.stat().st_mode | owner_mode)


def _playwright_profile_replace(
    *,
    source_profile_path: Path,
    target_profile_path: Path,
    write_enable: bool,
) -> None:
    """Prepare and atomically publish one exact profile directory.

    Args:
        source_profile_path: Existing profile directory to copy.
        target_profile_path: Exact directory path to publish.
        write_enable: Whether to make the staged tree owner-writable.

    Raises:
        FileNotFoundError: If the source profile directory is missing.
    """

    if not source_profile_path.is_dir():
        raise FileNotFoundError(f"profile directory is missing: {source_profile_path}")
    target_profile_path.parent.mkdir(parents=True, exist_ok=True)
    temp_profile_path = Path(
        tempfile.mkdtemp(
            dir=target_profile_path.parent,
            prefix=f".{target_profile_path.name}.",
            suffix=".tmp",
        )
    )
    try:
        _directory_tree_copy(source_profile_path, temp_profile_path)
        if write_enable:
            _directory_tree_write_enable(temp_profile_path)
        _directory_tree_atomic_replace(temp_profile_path, target_profile_path)
    finally:
        if temp_profile_path.exists():
            shutil.rmtree(temp_profile_path)


def playwright_profile_materialize(data_source_path: Path, target_profile_path: Path) -> None:
    """Materialize DataSource playwright_profile into a pod-local profile directory.

    Args:
        data_source_path: DataSource root containing playwright_profile.
        target_profile_path: Pod-local profile directory to create.
    """

    if target_profile_path.is_symlink() or (target_profile_path.exists() and not target_profile_path.is_dir()):
        raise ValueError(f"profile target must be a regular directory: {target_profile_path}")
    if not target_profile_path.exists():
        source_profile_path = data_source_path / "playwright_profile"
        if source_profile_path.exists() or source_profile_path.is_symlink():
            _playwright_profile_replace(
                source_profile_path=source_profile_path,
                target_profile_path=target_profile_path,
                write_enable=True,
            )
            return
        target_profile_path.mkdir(parents=True)
    for singleton_name in _CHROMIUM_SINGLETON_NAME_TUPLE:
        (target_profile_path / singleton_name).unlink(missing_ok=True)
    _directory_tree_validate(target_profile_path)
    _directory_tree_write_enable(target_profile_path)


def playwright_profile_replace(
    *,
    source_profile_path: Path,
    target_profile_path: Path,
) -> None:
    """Atomically replace one exact profile directory from a source tree.

    Args:
        source_profile_path: Existing profile directory to copy.
        target_profile_path: Exact directory path to publish.

    Raises:
        FileNotFoundError: If the source profile directory is missing.
    """

    _playwright_profile_replace(
        source_profile_path=source_profile_path,
        target_profile_path=target_profile_path,
        write_enable=True,
    )


def playwright_profile_snapshot(
    *,
    runtime_profile_path: Path,
    writeback_candidate_path: Path,
) -> None:
    """Publish a pod-local profile at one caller-owned writeback candidate path.

    Args:
        runtime_profile_path: Runtime profile directory to snapshot.
        writeback_candidate_path: Exact writeback candidate directory to publish.
    """

    _playwright_profile_replace(
        source_profile_path=runtime_profile_path,
        target_profile_path=writeback_candidate_path,
        write_enable=False,
    )
