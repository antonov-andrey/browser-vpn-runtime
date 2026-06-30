"""Playwright persistent profile directory helpers."""

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict


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


def playwright_profile_materialize(data_source_path: Path, target_profile_path: Path) -> PlaywrightProfileState:
    """Copy DataSource playwright_profile into a pod-local profile directory.

    Args:
        data_source_path: DataSource root containing playwright_profile.
        target_profile_path: Pod-local profile directory to create.

    Returns:
        Materialized profile state.
    """

    source_profile_path = data_source_path / "playwright_profile"
    if not source_profile_path.exists():
        if target_profile_path.exists():
            shutil.rmtree(target_profile_path)
        target_profile_path.mkdir(parents=True)
        return PlaywrightProfileState(file_path_list=[], profile_path=target_profile_path)
    file_path_list = _directory_tree_copy(source_profile_path, target_profile_path)
    return PlaywrightProfileState(file_path_list=file_path_list, profile_path=target_profile_path)


def playwright_profile_snapshot(runtime_profile_path: Path, data_source_path: Path) -> PlaywrightProfileState:
    """Copy a pod-local profile directory back to DataSource playwright_profile.

    Args:
        runtime_profile_path: Runtime profile directory to snapshot.
        data_source_path: DataSource root that receives playwright_profile.

    Returns:
        Snapshotted profile state.
    """

    target_profile_path = data_source_path / "playwright_profile"
    file_path_list = _directory_tree_copy(runtime_profile_path, target_profile_path)
    return PlaywrightProfileState(file_path_list=file_path_list, profile_path=target_profile_path)
