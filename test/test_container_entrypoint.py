"""Behavior tests for the least-privilege browser container entrypoint."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from browser_runtime.container_entrypoint import container_command_exec


class CommandExecIntercepted(RuntimeError):
    """Signal that a test intercepted the final process replacement."""


def test_container_entrypoint_prepares_owned_roots_before_privilege_drop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prepare only declared writable roots before executing as the browser user.

    Args:
        monkeypatch: Pytest replacement helper.
        tmp_path: Temporary filesystem root.
    """

    writable_path_list = [tmp_path / "output" / ".playwright-mcp", tmp_path / "runtime"]
    call_list: list[tuple[str, object]] = []
    runtime_user = SimpleNamespace(pw_dir="/home/browser", pw_gid=1000, pw_name="browser", pw_uid=1000)

    monkeypatch.setattr("pwd.getpwnam", lambda user_name: runtime_user)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "initgroups", lambda user_name, gid: call_list.append(("initgroups", (user_name, gid))))
    monkeypatch.setattr(os, "lchown", lambda path, uid, gid: call_list.append(("lchown", (Path(path), uid, gid))))
    monkeypatch.setattr(os, "setgid", lambda gid: call_list.append(("setgid", gid)))
    monkeypatch.setattr(os, "setuid", lambda uid: call_list.append(("setuid", uid)))
    monkeypatch.setenv("HOME", "/before")
    monkeypatch.setenv("LOGNAME", "before")
    monkeypatch.setenv("USER", "before")

    def command_exec(executable: str, command_argv: list[str]) -> None:
        """Capture the final command instead of replacing pytest.

        Args:
            executable: Command executable name.
            command_argv: Complete command argument list.

        Raises:
            CommandExecIntercepted: Always, after capturing the command.
        """

        call_list.append(("execvp", (executable, command_argv)))
        raise CommandExecIntercepted

    monkeypatch.setattr(os, "execvp", command_exec)

    with pytest.raises(CommandExecIntercepted):
        container_command_exec(
            command_argv=["browser-runtime-playwright-mcp-router", "--port", "8931"],
            runtime_user_name="browser",
            writable_path_list=writable_path_list,
        )

    assert all(path.is_dir() for path in writable_path_list)
    assert call_list[-4:] == [
        ("initgroups", ("browser", 1000)),
        ("setgid", 1000),
        ("setuid", 1000),
        (
            "execvp",
            (
                "browser-runtime-playwright-mcp-router",
                ["browser-runtime-playwright-mcp-router", "--port", "8931"],
            ),
        ),
    ]
    assert os.environ["HOME"] == "/home/browser"
    assert os.environ["LOGNAME"] == "browser"
    assert os.environ["USER"] == "browser"


def test_container_entrypoint_preserves_platform_supplied_browser_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run directly when the platform already selected the browser user.

    Args:
        monkeypatch: Pytest replacement helper.
        tmp_path: Temporary filesystem root.
    """

    writable_path = tmp_path / ".playwright-mcp"
    runtime_user = SimpleNamespace(pw_dir="/home/browser", pw_gid=1000, pw_name="browser", pw_uid=1000)
    monkeypatch.setattr("pwd.getpwnam", lambda user_name: runtime_user)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(os, "initgroups", lambda user_name, gid: pytest.fail("must not replace platform identity"))
    monkeypatch.setattr(os, "lchown", lambda path, uid, gid: pytest.fail("must not chown platform-owned volumes"))
    monkeypatch.setattr(os, "setgid", lambda gid: pytest.fail("must not replace platform identity"))
    monkeypatch.setattr(os, "setuid", lambda uid: pytest.fail("must not replace platform identity"))
    monkeypatch.setenv("HOME", "/before")

    def command_exec(executable: str, command_argv: list[str]) -> None:
        """Intercept the platform-user command execution.

        Args:
            executable: Command executable name.
            command_argv: Complete command argument list.

        Raises:
            CommandExecIntercepted: Always, after validating the command.
        """

        assert executable == "browser-runtime-playwright-mcp-router"
        assert command_argv == ["browser-runtime-playwright-mcp-router"]
        raise CommandExecIntercepted

    monkeypatch.setattr(os, "execvp", command_exec)

    with pytest.raises(CommandExecIntercepted):
        container_command_exec(
            command_argv=["browser-runtime-playwright-mcp-router"],
            runtime_user_name="browser",
            writable_path_list=[writable_path],
        )

    assert writable_path.is_dir()
    assert os.environ["HOME"] == "/home/browser"


def test_container_entrypoint_rejects_unexpected_platform_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject a platform UID that is neither root nor the browser user.

    Args:
        monkeypatch: Pytest replacement helper.
        tmp_path: Temporary filesystem root.
    """

    runtime_user = SimpleNamespace(pw_dir="/home/browser", pw_gid=1000, pw_name="browser", pw_uid=1000)
    monkeypatch.setattr("pwd.getpwnam", lambda user_name: runtime_user)
    monkeypatch.setattr(os, "geteuid", lambda: 2000)

    with pytest.raises(PermissionError, match="expected root or browser uid 1000"):
        container_command_exec(
            command_argv=["browser-runtime-playwright-mcp-router"],
            runtime_user_name="browser",
            writable_path_list=[tmp_path / ".playwright-mcp"],
        )
