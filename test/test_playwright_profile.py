"""Tests for Playwright profile materialization and snapshot helpers."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import stat
import threading

import pytest

from browser_runtime import playwright_profile
from browser_runtime.playwright_profile import (
    playwright_profile_materialize,
    playwright_profile_replace,
    playwright_profile_snapshot,
)


def test_playwright_profile_replace_atomically_replaces_target(tmp_path: Path) -> None:
    """Replace an existing profile at its exact published directory path."""
    source_profile_path = tmp_path / "source"
    target_profile_path = tmp_path / "target"
    (source_profile_path / "Default").mkdir(parents=True)
    (source_profile_path / "Default" / "Cookies").write_text("new", encoding="utf-8")
    target_profile_path.mkdir()
    (target_profile_path / "stale").write_text("old", encoding="utf-8")

    playwright_profile_replace(
        source_profile_path=source_profile_path,
        target_profile_path=target_profile_path,
    )

    assert (target_profile_path / "Default" / "Cookies").read_text(encoding="utf-8") == "new"
    assert not (target_profile_path / "stale").exists()


def test_playwright_profile_replace_fails_when_source_is_missing(tmp_path: Path) -> None:
    """Reject replacement when the requested source profile does not exist."""
    source_profile_path = tmp_path / "missing"
    target_profile_path = tmp_path / "target"

    with pytest.raises(FileNotFoundError, match=f"profile directory is missing: {source_profile_path}"):
        playwright_profile_replace(
            source_profile_path=source_profile_path,
            target_profile_path=target_profile_path,
        )

    assert not target_profile_path.exists()


def test_playwright_profile_replace_publishes_initial_target(tmp_path: Path) -> None:
    """Publish the first profile at the exact requested target directory."""
    source_profile_path = tmp_path / "source"
    source_profile_path.mkdir()
    (source_profile_path / "Preferences").write_text("new", encoding="utf-8")
    target_profile_path = tmp_path / "target"

    playwright_profile_replace(
        source_profile_path=source_profile_path,
        target_profile_path=target_profile_path,
    )

    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "new"


def test_playwright_profile_replace_excludes_chromium_singletons(tmp_path: Path) -> None:
    """Exclude Chromium singleton markers from the published profile tree."""
    source_profile_path = tmp_path / "source"
    source_profile_path.mkdir()
    (source_profile_path / "Preferences").write_text("new", encoding="utf-8")
    for singleton_name in ["SingletonCookie", "SingletonLock", "SingletonSocket"]:
        (source_profile_path / singleton_name).symlink_to("stale")
    target_profile_path = tmp_path / "target"

    playwright_profile_replace(
        source_profile_path=source_profile_path,
        target_profile_path=target_profile_path,
    )

    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "new"
    assert not any(
        (target_profile_path / singleton_name).exists() or (target_profile_path / singleton_name).is_symlink()
        for singleton_name in ["SingletonCookie", "SingletonLock", "SingletonSocket"]
    )


def test_playwright_profile_replace_rejects_source_symlink_without_changing_target(tmp_path: Path) -> None:
    """Do not dereference profile entries outside the declared source tree."""

    external_path = tmp_path / "external"
    external_path.mkdir()
    (external_path / "Cookies").write_text("external", encoding="utf-8")
    source_profile_path = tmp_path / "source"
    source_profile_path.mkdir()
    (source_profile_path / "Default").symlink_to(external_path, target_is_directory=True)
    target_profile_path = tmp_path / "target"
    target_profile_path.mkdir()
    (target_profile_path / "Preferences").write_text("previous", encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        playwright_profile_replace(
            source_profile_path=source_profile_path,
            target_profile_path=target_profile_path,
        )

    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "previous"
    assert (external_path / "Cookies").read_text(encoding="utf-8") == "external"


def test_playwright_profile_replace_uses_unique_sibling_temp_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep simultaneous replacement staging directories distinct and target-adjacent."""
    target_profile_path = tmp_path / "target"
    target_profile_path.mkdir()
    (target_profile_path / "Preferences").write_text("old", encoding="utf-8")
    source_profile_path_list = [tmp_path / "source-a", tmp_path / "source-b"]
    for index, source_profile_path in enumerate(source_profile_path_list):
        source_profile_path.mkdir()
        (source_profile_path / "Preferences").write_text(f"new-{index}", encoding="utf-8")
    barrier = threading.Barrier(2)
    staged_profile_path_list: list[Path] = []
    real_directory_tree_atomic_replace = playwright_profile._directory_tree_atomic_replace

    def synchronized_directory_tree_atomic_replace(source_path: Path, target_path: Path) -> None:
        """Hold both prepared trees before allowing either publication."""
        staged_profile_path_list.append(source_path)
        assert source_path.parent == target_path.parent
        barrier.wait(timeout=5)
        real_directory_tree_atomic_replace(source_path, target_path)

    monkeypatch.setattr(
        playwright_profile,
        "_directory_tree_atomic_replace",
        synchronized_directory_tree_atomic_replace,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda source_profile_path: playwright_profile_replace(
                    source_profile_path=source_profile_path,
                    target_profile_path=target_profile_path,
                ),
                source_profile_path_list,
            )
        )

    assert len(set(staged_profile_path_list)) == 2
    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") in {"new-0", "new-1"}


def test_playwright_profile_materialize_copies_directory_tree(tmp_path: Path) -> None:
    """Materialize a playwright_profile directory tree into a pod-local directory."""
    source_profile_path = tmp_path / "secret-root" / "playwright_profile"
    source_profile_path.mkdir(parents=True)
    (source_profile_path / "Preferences").write_text("prefs", encoding="utf-8")
    (source_profile_path / "Default").mkdir()
    (source_profile_path / "Default" / "Cookies").write_text("cookies", encoding="utf-8")
    target_profile_path = tmp_path / "runtime-profile"

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert sorted(path for path in target_profile_path.rglob("*") if path.is_file()) == [
        target_profile_path / "Default" / "Cookies",
        target_profile_path / "Preferences",
    ]
    assert (target_profile_path / "Default" / "Cookies").read_text(encoding="utf-8") == "cookies"


def test_playwright_profile_materialize_makes_immutable_source_tree_writable(tmp_path: Path) -> None:
    """Make a read-only secret root snapshot writable only in its runtime copy."""

    source_profile_path = tmp_path / "secret-root" / "playwright_profile"
    source_default_path = source_profile_path / "Default"
    source_default_path.mkdir(parents=True)
    (source_default_path / "Preferences").write_text("{}", encoding="utf-8")
    source_default_path.chmod(0o555)
    source_profile_path.chmod(0o555)
    target_profile_path = tmp_path / "runtime-profile"

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert target_profile_path.stat().st_mode & stat.S_IWUSR
    assert (target_profile_path / "Default").stat().st_mode & stat.S_IWUSR
    assert not source_profile_path.stat().st_mode & stat.S_IWUSR


def test_playwright_profile_materialize_stages_writable_tree_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Make the staged runtime tree writable before publishing its directory name."""

    source_profile_path = tmp_path / "secret-root" / "playwright_profile"
    source_default_path = source_profile_path / "Default"
    source_default_path.mkdir(parents=True)
    source_preferences_path = source_default_path / "Preferences"
    source_preferences_path.write_text("{}", encoding="utf-8")
    source_preferences_path.chmod(0o444)
    source_default_path.chmod(0o555)
    source_profile_path.chmod(0o555)
    target_profile_path = tmp_path / "runtime-profile"
    real_directory_tree_atomic_replace = playwright_profile._directory_tree_atomic_replace

    def writable_directory_tree_atomic_replace(source_path: Path, target_path: Path) -> None:
        """Observe writable staged modes immediately before atomic publication."""

        assert source_path.stat().st_mode & stat.S_IWUSR
        assert (source_path / "Default").stat().st_mode & stat.S_IWUSR
        assert (source_path / "Default" / "Preferences").stat().st_mode & stat.S_IWUSR
        assert not source_profile_path.stat().st_mode & stat.S_IWUSR
        assert not source_default_path.stat().st_mode & stat.S_IWUSR
        assert not source_preferences_path.stat().st_mode & stat.S_IWUSR
        real_directory_tree_atomic_replace(source_path, target_path)

    monkeypatch.setattr(
        playwright_profile,
        "_directory_tree_atomic_replace",
        writable_directory_tree_atomic_replace,
    )

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert target_profile_path.stat().st_mode & stat.S_IWUSR
    assert not source_profile_path.stat().st_mode & stat.S_IWUSR


def test_playwright_profile_materialize_creates_empty_profile_when_source_is_absent(tmp_path: Path) -> None:
    """Materialize an empty pod-local profile when secret root has no playwright_profile prefix."""
    target_profile_path = tmp_path / "runtime-profile"

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert target_profile_path.is_dir()
    assert not list(target_profile_path.iterdir())


def test_playwright_profile_materialize_preserves_existing_runtime_profile(tmp_path: Path) -> None:
    """Keep an already materialized runtime profile unchanged within one workflow run."""
    source_profile_path = tmp_path / "secret-root" / "playwright_profile"
    source_profile_path.mkdir(parents=True)
    (source_profile_path / "Preferences").write_text("source", encoding="utf-8")
    target_profile_path = tmp_path / "runtime-profile"
    target_profile_path.mkdir()
    (target_profile_path / "Preferences").write_text("runtime", encoding="utf-8")

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"


def test_playwright_profile_materialize_removes_stale_chromium_singletons(tmp_path: Path) -> None:
    """Remove stale Chromium singleton markers before reusing one runtime profile."""
    target_profile_path = tmp_path / "runtime-profile"
    target_profile_path.mkdir()
    (target_profile_path / "Preferences").write_text("runtime", encoding="utf-8")
    for singleton_name in ["SingletonCookie", "SingletonLock", "SingletonSocket"]:
        (target_profile_path / singleton_name).symlink_to("stale")

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"
    assert not any(
        (target_profile_path / singleton_name).exists() or (target_profile_path / singleton_name).is_symlink()
        for singleton_name in ["SingletonCookie", "SingletonLock", "SingletonSocket"]
    )


def test_playwright_profile_materialize_removes_regular_singleton(tmp_path: Path) -> None:
    """Remove a stale regular singleton file before profile reuse."""

    target_profile_path = tmp_path / "runtime-profile"
    target_profile_path.mkdir()
    preferences_path = target_profile_path / "Preferences"
    preferences_path.write_text("runtime", encoding="utf-8")
    singleton_path = target_profile_path / "SingletonLock"
    singleton_path.write_text("stale", encoding="utf-8")

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert preferences_path.read_text(encoding="utf-8") == "runtime"
    assert not singleton_path.exists()


def test_playwright_profile_materialize_removes_singletons_copied_from_source(tmp_path: Path) -> None:
    """Remove stale Chromium singleton markers after fresh profile materialization."""
    source_profile_path = tmp_path / "secret-root" / "playwright_profile"
    source_profile_path.mkdir(parents=True)
    (source_profile_path / "Preferences").write_text("source", encoding="utf-8")
    for singleton_name in ["SingletonCookie", "SingletonLock", "SingletonSocket"]:
        (source_profile_path / singleton_name).symlink_to("stale")
    target_profile_path = tmp_path / "runtime-profile"

    playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "source"
    assert not any(
        (target_profile_path / singleton_name).exists() or (target_profile_path / singleton_name).is_symlink()
        for singleton_name in ["SingletonCookie", "SingletonLock", "SingletonSocket"]
    )


def test_playwright_profile_materialize_rejects_symlink_target(tmp_path: Path) -> None:
    """Do not mutate a directory reached through a caller-provided target symlink."""

    external_path = tmp_path / "external"
    external_path.mkdir()
    external_file_path = external_path / "Preferences"
    external_file_path.write_text("external", encoding="utf-8")
    target_profile_path = tmp_path / "runtime-profile"
    target_profile_path.symlink_to(external_path, target_is_directory=True)

    with pytest.raises(ValueError, match="profile target must be a regular directory"):
        playwright_profile_materialize(tmp_path / "secret-root", target_profile_path)

    assert target_profile_path.is_symlink()
    assert external_file_path.read_text(encoding="utf-8") == "external"


def test_playwright_profile_snapshot_replaces_caller_writeback_candidate(tmp_path: Path) -> None:
    """Snapshot a runtime profile directly to the caller-provided candidate path."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Default").mkdir()
    (runtime_profile_path / "Default" / "Local Storage").write_text("storage", encoding="utf-8")
    writeback_candidate_path = tmp_path / "runtime" / "mcp_playwright_profile" / "writeback_candidate"

    playwright_profile_snapshot(
        runtime_profile_path=runtime_profile_path,
        writeback_candidate_path=writeback_candidate_path,
    )

    snapshot_file_path = writeback_candidate_path / "Default" / "Local Storage"
    assert snapshot_file_path.read_text(encoding="utf-8") == "storage"


def test_playwright_profile_snapshot_exchanges_before_cleanup_and_parent_fsync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Publish an existing profile with one exchange before cleanup and parent fsync."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Preferences").write_text("runtime", encoding="utf-8")
    target_profile_path = tmp_path / "writeback-candidate"
    target_profile_path.mkdir(parents=True)
    (target_profile_path / "Preferences").write_text("previous", encoding="utf-8")
    event_list: list[str] = []
    real_directory_tree_exchange = playwright_profile._directory_tree_exchange

    def fake_directory_tree_exchange(source_path: Path, target_path: Path) -> None:
        """Record the atomic exchange while both directory names remain present."""
        event_list.append("exchange")
        assert (target_path / "Preferences").read_text(encoding="utf-8") == "previous"
        real_directory_tree_exchange(source_path, target_path)
        assert (target_path / "Preferences").read_text(encoding="utf-8") == "runtime"
        assert (source_path / "Preferences").read_text(encoding="utf-8") == "previous"

    real_rmtree = shutil.rmtree

    def fake_rmtree(path: Path) -> None:
        """Remove the exchanged old tree only while the new target remains published."""
        event_list.append("remove")
        assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"
        assert (path / "Preferences").read_text(encoding="utf-8") == "previous"
        real_rmtree(path)

    def fake_directory_fsync(path: Path) -> None:
        """Record parent fsync after the exchanged old tree is removed."""
        event_list.append("fsync")
        assert path == tmp_path
        assert target_profile_path.is_dir()
        assert not any(path.name.startswith(".writeback-candidate.") for path in tmp_path.iterdir())

    monkeypatch.setattr(playwright_profile, "_directory_tree_exchange", fake_directory_tree_exchange)
    monkeypatch.setattr(playwright_profile.shutil, "rmtree", fake_rmtree)
    monkeypatch.setattr(playwright_profile, "_directory_fsync", fake_directory_fsync)

    playwright_profile_snapshot(
        runtime_profile_path=runtime_profile_path,
        writeback_candidate_path=target_profile_path,
    )

    assert event_list == ["exchange", "remove", "fsync"]
    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"


def test_playwright_profile_snapshot_preserves_existing_target_when_exchange_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the previous profile continuously published when atomic exchange fails."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Preferences").write_text("runtime", encoding="utf-8")
    target_profile_path = tmp_path / "writeback-candidate"
    target_profile_path.mkdir(parents=True)
    (target_profile_path / "Preferences").write_text("previous", encoding="utf-8")

    def fake_directory_tree_exchange(source_path: Path, target_path: Path) -> None:
        """Fail before namespace publication without removing either directory."""
        assert source_path.is_dir()
        assert target_path.is_dir()
        raise OSError("exchange failed")

    monkeypatch.setattr(playwright_profile, "_directory_tree_exchange", fake_directory_tree_exchange)

    with pytest.raises(OSError, match="exchange failed"):
        playwright_profile_snapshot(
            runtime_profile_path=runtime_profile_path,
            writeback_candidate_path=target_profile_path,
        )

    assert target_profile_path.is_dir()
    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "previous"
    assert not any(path.name.startswith(".writeback-candidate.") for path in tmp_path.iterdir())


def test_playwright_profile_snapshot_keeps_new_target_when_old_tree_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the new target published if cleanup fails after the atomic exchange."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Preferences").write_text("runtime", encoding="utf-8")
    target_profile_path = tmp_path / "writeback-candidate"
    target_profile_path.mkdir(parents=True)
    (target_profile_path / "Preferences").write_text("previous", encoding="utf-8")
    fsync_path_list: list[Path] = []

    def fake_rmtree(path: Path) -> None:
        """Simulate cleanup failure after publication."""
        assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"
        assert (path / "Preferences").read_text(encoding="utf-8") == "previous"
        raise OSError("cleanup failed")

    monkeypatch.setattr(playwright_profile.shutil, "rmtree", fake_rmtree)
    monkeypatch.setattr(playwright_profile, "_directory_fsync", fsync_path_list.append)

    with pytest.raises(OSError, match="cleanup failed"):
        playwright_profile_snapshot(
            runtime_profile_path=runtime_profile_path,
            writeback_candidate_path=target_profile_path,
        )

    assert fsync_path_list == [tmp_path]
    assert target_profile_path.is_dir()
    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"


def test_playwright_profile_snapshot_uses_replace_when_target_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Publish a first profile with one replace and one parent fsync."""
    runtime_profile_path = tmp_path / "runtime-profile"
    runtime_profile_path.mkdir()
    (runtime_profile_path / "Preferences").write_text("runtime", encoding="utf-8")
    target_profile_path = tmp_path / "writeback-candidate"
    event_list: list[str] = []
    real_replace = playwright_profile.os.replace

    def fake_replace(source_path: Path, target_path: Path) -> None:
        """Record first publication through os.replace."""
        event_list.append("replace")
        real_replace(source_path, target_path)

    def fail_directory_tree_exchange(source_path: Path, target_path: Path) -> None:
        """Reject an exchange for an absent target."""
        raise AssertionError(f"unexpected exchange: {source_path} -> {target_path}")

    monkeypatch.setattr(playwright_profile.os, "replace", fake_replace)
    monkeypatch.setattr(playwright_profile, "_directory_tree_exchange", fail_directory_tree_exchange)
    monkeypatch.setattr(playwright_profile, "_directory_fsync", lambda path: event_list.append("fsync"))

    playwright_profile_snapshot(
        runtime_profile_path=runtime_profile_path,
        writeback_candidate_path=target_profile_path,
    )

    assert event_list == ["replace", "fsync"]
    assert (target_profile_path / "Preferences").read_text(encoding="utf-8") == "runtime"


def test_playwright_profile_existing_target_requires_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reject non-Linux replacement when an existing directory needs exchange semantics."""
    source_path = tmp_path / "source"
    source_path.mkdir()
    target_path = tmp_path / "target"
    target_path.mkdir()
    monkeypatch.setattr(playwright_profile.sys, "platform", "darwin")

    with pytest.raises(RuntimeError, match="Linux"):
        playwright_profile._directory_tree_atomic_replace(source_path, target_path)
