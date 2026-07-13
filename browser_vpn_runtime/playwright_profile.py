"""Playwright persistent profile directory helpers."""

import argparse
import ctypes
import os
import shutil
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

AT_FDCWD = -100
CHROMIUM_SINGLETON_NAME_LIST = ["SingletonCookie", "SingletonLock", "SingletonSocket"]
RENAME_EXCHANGE = 2


class PlaywrightProfileSnapshotConfig(BaseModel):
    """Validated executable profile snapshot configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    owner_gid: int | None = Field(default=None, ge=0)
    owner_uid: int | None = Field(default=None, ge=0)
    runtime_profile_path: Path
    writeback_candidate_path: Path


class PlaywrightProfileState(BaseModel):
    """Materialized or snapshotted Playwright profile state."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    file_path_list: list[Path]
    profile_path: Path


def _args_parse() -> argparse.Namespace:
    """Parse the profile snapshot CLI arguments.

    Returns:
        Parsed CLI namespace.
    """

    parser = argparse.ArgumentParser(description="Atomically snapshot a runtime Playwright profile.")
    parser.add_argument("--owner-gid", type=int)
    parser.add_argument("--owner-uid", type=int)
    parser.add_argument("--runtime-profile-path", required=True, type=Path)
    parser.add_argument("--writeback-candidate-path", required=True, type=Path)
    return parser.parse_args()


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


def _directory_tree_copy(source_path: Path, target_path: Path) -> list[Path]:
    """Copy one directory tree and return copied file paths.

    Args:
        source_path: Existing source directory.
        target_path: Destination directory.

    Returns:
        Copied destination file paths in stable order.
    """

    shutil.copytree(
        source_path,
        target_path,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*CHROMIUM_SINGLETON_NAME_LIST),
    )
    return sorted(path for path in target_path.rglob("*") if path.is_file())


def _directory_tree_atomic_replace(source_path: Path, target_path: Path) -> None:
    """Publish one prepared directory without removing an existing target name.

    Args:
        source_path: Prepared sibling directory tree.
        target_path: Published target directory tree.

    Raises:
        RuntimeError: If an existing target must be exchanged outside Linux.
    """

    if not target_path.exists():
        os.replace(source_path, target_path)
        _directory_fsync(target_path.parent)
        return
    if sys.platform != "linux":
        raise RuntimeError("atomic replacement of an existing profile directory requires Linux renameat2")
    _directory_tree_exchange(source_path, target_path)
    try:
        _directory_tree_remove(source_path)
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
        AT_FDCWD,
        os.fsencode(source_path),
        AT_FDCWD,
        os.fsencode(target_path),
        RENAME_EXCHANGE,
    )
    if result != 0:
        errno_value = ctypes.get_errno()
        raise OSError(errno_value, os.strerror(errno_value), f"{source_path} <-> {target_path}")


def _directory_tree_owner_set(path: Path, owner_uid: int | None, owner_gid: int | None) -> None:
    """Set owner on one directory tree before it is published.

    Args:
        path: Directory tree root.
        owner_uid: Target owner user id, or `None` to preserve it.
        owner_gid: Target owner group id, or `None` to preserve it.
    """
    if owner_uid is None and owner_gid is None:
        return
    uid = -1 if owner_uid is None else owner_uid
    gid = -1 if owner_gid is None else owner_gid
    os.chown(path, uid, gid)
    for child_path in path.rglob("*"):
        os.chown(child_path, uid, gid)


def _directory_tree_remove(path: Path) -> None:
    """Remove one unpublished directory tree.

    Args:
        path: Directory tree to remove.
    """

    shutil.rmtree(path)


def _playwright_profile_replace(
    *,
    source_profile_path: Path,
    target_profile_path: Path,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> PlaywrightProfileState:
    """Prepare and atomically publish one exact profile directory.

    Args:
        source_profile_path: Existing profile directory to copy.
        target_profile_path: Exact directory path to publish.
        owner_uid: Target owner user id to set before publishing.
        owner_gid: Target owner group id to set before publishing.

    Returns:
        Published profile state.

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
        _directory_tree_owner_set(temp_profile_path, owner_uid, owner_gid)
        _directory_tree_atomic_replace(temp_profile_path, target_profile_path)
    finally:
        if temp_profile_path.exists():
            _directory_tree_remove(temp_profile_path)
    return PlaywrightProfileState(
        file_path_list=sorted(path for path in target_profile_path.rglob("*") if path.is_file()),
        profile_path=target_profile_path,
    )


def playwright_profile_materialize(data_source_path: Path, target_profile_path: Path) -> PlaywrightProfileState:
    """Materialize DataSource playwright_profile into a pod-local profile directory.

    Args:
        data_source_path: DataSource root containing playwright_profile.
        target_profile_path: Pod-local profile directory to create.

    Returns:
        Materialized profile state.
    """

    if not target_profile_path.exists():
        source_profile_path = data_source_path / "playwright_profile"
        if source_profile_path.exists():
            return playwright_profile_replace(
                source_profile_path=source_profile_path,
                target_profile_path=target_profile_path,
            )
        else:
            target_profile_path.mkdir(parents=True)
    for singleton_name in CHROMIUM_SINGLETON_NAME_LIST:
        (target_profile_path / singleton_name).unlink(missing_ok=True)
    return PlaywrightProfileState(
        file_path_list=sorted(path for path in target_profile_path.rglob("*") if path.is_file()),
        profile_path=target_profile_path,
    )


def playwright_profile_replace(
    *,
    source_profile_path: Path,
    target_profile_path: Path,
) -> PlaywrightProfileState:
    """Atomically replace one exact profile directory from a source tree.

    Args:
        source_profile_path: Existing profile directory to copy.
        target_profile_path: Exact directory path to publish.

    Returns:
        Published profile state.

    Raises:
        FileNotFoundError: If the source profile directory is missing.
    """

    return _playwright_profile_replace(
        source_profile_path=source_profile_path,
        target_profile_path=target_profile_path,
    )


def playwright_profile_snapshot(
    *,
    runtime_profile_path: Path,
    writeback_candidate_path: Path,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> PlaywrightProfileState:
    """Publish a pod-local profile at one caller-owned writeback candidate path.

    Args:
        runtime_profile_path: Runtime profile directory to snapshot.
        writeback_candidate_path: Exact writeback candidate directory to publish.
        owner_uid: Host owner user id to set before publishing.
        owner_gid: Host owner group id to set before publishing.

    Returns:
        Snapshotted profile state.
    """

    return _playwright_profile_replace(
        source_profile_path=runtime_profile_path,
        target_profile_path=writeback_candidate_path,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )


def main() -> int:
    """Run the generic profile snapshot executable boundary.

    Returns:
        Process exit code.
    """

    config = PlaywrightProfileSnapshotConfig(**vars(_args_parse()))
    state = playwright_profile_snapshot(
        owner_gid=config.owner_gid,
        owner_uid=config.owner_uid,
        runtime_profile_path=config.runtime_profile_path,
        writeback_candidate_path=config.writeback_candidate_path,
    )
    print(state.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
