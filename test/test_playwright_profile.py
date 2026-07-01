"""Tests for Playwright profile materialization and snapshot helpers."""

from pathlib import Path
import shutil

import pytest

from browser_vpn_runtime import playwright_profile
from browser_vpn_runtime.playwright_profile import playwright_profile_materialize, playwright_profile_snapshot


def test_playwright_profile_materialize_copies_directory_tree(tmp_path: Path) -> None:
    """Materialize a playwright_profile directory tree into a pod-local directory."""
    source_profile_path = tmp_path / "data-source" / "playwright_profile"
    source_profile_path.mkdir(parents=True)
    (source_profile_path / "Preferences").write_text("prefs", encoding="utf-8")
    (source_profile_path / "Default").mkdir()
    (source_profile_path / "Default" / "Cookies").write_text("cookies", encoding="utf-8")
    target_profile_path = tmp_path / "runtime-profile"

    state = playwright_profile_materialize(tmp_path / "data-source", target_profile_path)

    assert state.profile_path == target_profile_path
    assert sorted(state.file_path_list) == [
        target_profile_path / "Default" / "Cookies",
        target_profile_path / "Preferences",
    ]
    assert (target_profile_path / "Default" / "Cookies").read_text(encoding="utf-8") == "cookies"


def test_playwright_profile_materialize_creates_empty_profile_when_source_is_absent(tmp_path: Path) -> None:
    """Materialize an empty pod-local profile when DataSource has no playwright_profile prefix."""
    target_profile_path = tmp_path / "runtime-profile"

    state = playwright_profile_materialize(tmp_path / "data-source", target_profile_path)

    assert state.file_path_list == []
    assert state.profile_path == target_profile_path
    assert target_profile_path.is_dir()


def test_playwright_profile_materialize_preserves_existing_runtime_profile(tmp_path: Path) -> None:
    """Keep an already materialized runtime profile unchanged within one workflow run."""
    source_profile_path = tmp_path / "data-source" / "playwright_profile"
    source_profile_path.mkdir(parents=True)
    (source_profile_path / "Preferences").write_text("source", encoding="utf-8")
    target_profile_path = tmp_path / "runtime-profile"
    target_profile_path.mkdir()
    (target_profile_path / "Preferences").write_text("runtime", encoding="utf-8")

    state = playwright_profile_materialize(tmp_path / "data-source", target_profile_path)

    assert state.file_path_list == [target_profile_path / "Preferences"]
    assert state.profile_path == target_profile_path
    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"


def test_playwright_profile_snapshot_copies_runtime_tree_back_to_data_source(tmp_path: Path) -> None:
    """Snapshot a runtime profile directory tree back under playwright_profile."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Default").mkdir()
    (runtime_profile_path / "Default" / "Local Storage").write_text("storage", encoding="utf-8")
    data_source_path = tmp_path / "data-source"

    state = playwright_profile_snapshot(runtime_profile_path, data_source_path)

    snapshot_file_path = data_source_path / "playwright_profile" / "Default" / "Local Storage"
    assert state.profile_path == data_source_path / "playwright_profile"
    assert state.file_path_list == [snapshot_file_path]
    assert snapshot_file_path.read_text(encoding="utf-8") == "storage"


def test_playwright_profile_snapshot_sets_owner_before_atomic_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prepare ownership on the temp tree before publishing it into DataSource."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Preferences").write_text("runtime", encoding="utf-8")
    data_source_path = tmp_path / "data-source"
    event_list: list[tuple[str, str, int | None, int | None]] = []

    def fake_directory_tree_owner_set(path: Path, owner_uid: int | None, owner_gid: int | None) -> None:
        """Record owner update and mark the temp tree."""
        event_list.append(("owner", path.name, owner_uid, owner_gid))
        (path / "owner.marker").write_text("owned", encoding="utf-8")

    def fake_directory_tree_atomic_replace(source_path: Path, target_path: Path) -> None:
        """Record atomic publish after the owner marker exists."""
        event_list.append(("replace", source_path.name, None, None))
        assert (source_path / "owner.marker").is_file()
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.copytree(source_path, target_path)

    monkeypatch.setattr(playwright_profile, "_directory_tree_owner_set", fake_directory_tree_owner_set)
    monkeypatch.setattr(playwright_profile, "_directory_tree_atomic_replace", fake_directory_tree_atomic_replace)

    state = playwright_profile_snapshot(runtime_profile_path, data_source_path, owner_uid=1000, owner_gid=1000)

    assert event_list == [
        ("owner", ".playwright_profile.tmp", 1000, 1000),
        ("replace", ".playwright_profile.tmp", None, None),
    ]
    assert state.profile_path == data_source_path / "playwright_profile"
    assert (state.profile_path / "owner.marker").read_text(encoding="utf-8") == "owned"
