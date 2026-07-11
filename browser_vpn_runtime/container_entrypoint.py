"""Least-privilege process boundary for the Playwright container image."""

import os
import pwd
import sys
from pathlib import Path
from typing import NoReturn

CONTAINER_RUNTIME_USER_NAME = "browser"
CONTAINER_WRITABLE_PATH_LIST = [Path("/output/.playwright-mcp"), Path("/runtime"), Path("/runtime-profile")]


def container_command_exec(
    command_argv: list[str],
    runtime_user_name: str,
    writable_path_list: list[Path],
) -> NoReturn:
    """Prepare declared writable roots and replace the process as a non-root user.

    Args:
        command_argv: Complete command argument list.
        runtime_user_name: Operating-system user that owns browser execution.
        writable_path_list: Exact container roots that browser processes may mutate.

    Raises:
        PermissionError: If a platform-selected user differs from the browser user.
        ValueError: If no command was supplied.
    """

    if not command_argv:
        raise ValueError("container command must not be empty")
    runtime_user = pwd.getpwnam(runtime_user_name)
    effective_uid = os.geteuid()
    if effective_uid not in {0, runtime_user.pw_uid}:
        raise PermissionError(
            f"container runs as uid {effective_uid}; expected root or browser uid {runtime_user.pw_uid}"
        )
    for writable_path in writable_path_list:
        writable_path.mkdir(parents=True, exist_ok=True)
        if effective_uid == 0:
            os.lchown(writable_path, runtime_user.pw_uid, runtime_user.pw_gid)
            for child_path in writable_path.rglob("*"):
                os.lchown(child_path, runtime_user.pw_uid, runtime_user.pw_gid)
    os.environ["HOME"] = runtime_user.pw_dir
    os.environ["LOGNAME"] = runtime_user.pw_name
    os.environ["USER"] = runtime_user.pw_name
    if effective_uid == 0:
        os.initgroups(runtime_user.pw_name, runtime_user.pw_gid)
        os.setgid(runtime_user.pw_gid)
        os.setuid(runtime_user.pw_uid)
    os.execvp(command_argv[0], command_argv)


def main() -> None:
    """Execute the image command after preparing browser-owned runtime roots."""

    container_command_exec(
        command_argv=sys.argv[1:],
        runtime_user_name=CONTAINER_RUNTIME_USER_NAME,
        writable_path_list=CONTAINER_WRITABLE_PATH_LIST,
    )


if __name__ == "__main__":
    main()
