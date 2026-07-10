"""Playwright persistent profile directory helpers."""

import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict
CHROMIUM_SINGLETON_NAME_LIST = ["SingletonCookie", "SingletonLock", "SingletonSocket"]



class PlaywrightProfileState(BaseModel):
    """Materialized or snapshotted Playwright profile state."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    file_path_list: list[Path]
    profile_path: Path


def _directory_tree_copy(source_path: Path, target_path: Path) -> list[Path]:
    """Copy one directory tree and return copied file paths.

    Args:
        source_path: Existing source directory.
        target_path: Destination directory.

    Returns:
        Copied destination file paths in stable order.
    """

    if not source_path.is_dir():
        raise FileNotFoundError(f"profile directory is missing: {source_path}")
    if target_path.exists():
        shutil.rmtree(target_path)
    shutil.copytree(source_path, target_path)
    return sorted(path for path in target_path.rglob("*") if path.is_file())


def _directory_tree_atomic_replace(source_path: Path, target_path: Path) -> None:
    """Atomically replace one directory tree with another prepared tree.

    Args:
        source_path: Prepared sibling directory tree.
        target_path: Published target directory tree.
    """
    backup_path = target_path.with_name(f".{target_path.name}.old")
    if backup_path.exists():
        shutil.rmtree(backup_path)
    try:
        if target_path.exists():
            os.replace(target_path, backup_path)
        os.replace(source_path, target_path)
    except Exception:
        if not target_path.exists() and backup_path.exists():
            os.replace(backup_path, target_path)
        raise
    finally:
        if backup_path.exists():
            shutil.rmtree(backup_path)


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


def playwright_profile_materialize(data_source_path: Path, target_profile_path: Path) -> PlaywrightProfileState:
    """Materialize DataSource playwright_profile into a pod-local profile directory.

    Args:
        data_source_path: DataSource root containing playwright_profile.
        target_profile_path: Pod-local profile directory to create.

    Returns:
        Materialized profile state.
    """

    if target_profile_path.exists():
        for singleton_name in CHROMIUM_SINGLETON_NAME_LIST:
            (target_profile_path / singleton_name).unlink(missing_ok=True)
        return PlaywrightProfileState(
            file_path_list=sorted(path for path in target_profile_path.rglob("*") if path.is_file()),
            profile_path=target_profile_path,
        )
    source_profile_path = data_source_path / "playwright_profile"
    if not source_profile_path.exists():
        target_profile_path.mkdir(parents=True)
        return PlaywrightProfileState(file_path_list=[], profile_path=target_profile_path)
        for singleton_name in CHROMIUM_SINGLETON_NAME_LIST:
            (target_profile_path / singleton_name).unlink(missing_ok=True)
    file_path_list = _directory_tree_copy(source_profile_path, target_profile_path)
    return PlaywrightProfileState(file_path_list=file_path_list, profile_path=target_profile_path)

    for singleton_name in CHROMIUM_SINGLETON_NAME_LIST:
        (target_profile_path / singleton_name).unlink(missing_ok=True)

def playwright_profile_snapshot(
    runtime_profile_path: Path,
    data_source_path: Path,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> PlaywrightProfileState:
    """Copy a pod-local profile directory back to DataSource playwright_profile.

    Args:
        runtime_profile_path: Runtime profile directory to snapshot.
        data_source_path: DataSource root that receives playwright_profile.
        owner_uid: Host owner user id to set before publishing.
        owner_gid: Host owner group id to set before publishing.

    Returns:
        Snapshotted profile state.
    """

    target_profile_path = data_source_path / "playwright_profile"
    temp_profile_path = data_source_path / ".playwright_profile.tmp"
    data_source_path.mkdir(parents=True, exist_ok=True)
    if temp_profile_path.exists():
        shutil.rmtree(temp_profile_path)
    try:
        _directory_tree_copy(runtime_profile_path, temp_profile_path)
        _directory_tree_owner_set(temp_profile_path, owner_uid, owner_gid)
        _directory_tree_atomic_replace(temp_profile_path, target_profile_path)
    finally:
        if temp_profile_path.exists():
            shutil.rmtree(temp_profile_path)
    file_path_list = sorted(path for path in target_profile_path.rglob("*") if path.is_file())
    return PlaywrightProfileState(file_path_list=file_path_list, profile_path=target_profile_path)
