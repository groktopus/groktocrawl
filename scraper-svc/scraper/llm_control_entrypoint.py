"""Pre-bind the private model-control UDS before Uvicorn starts app lifespan."""

from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

CONTROL_SOCKET = Path("/run/scraper-llm/control.sock")
SOCKET_DIRECTORY_UID = 10002
SOCKET_DIRECTORY_GID = 20000
SOCKET_DIRECTORY_MODE = 0o2710
SOCKET_MODE = 0o660


def bind_control_listener(
    path: Path = CONTROL_SOCKET,
    *,
    expected_directory_uid: int = SOCKET_DIRECTORY_UID,
    expected_directory_gid: int = SOCKET_DIRECTORY_GID,
    expected_directory_mode: int = SOCKET_DIRECTORY_MODE,
) -> socket.socket:
    """Create a pre-bound, private socket for Uvicorn's inherited-fd mode."""
    parent_stat = path.parent.lstat()
    if (
        stat.S_ISLNK(parent_stat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != expected_directory_uid
        or parent_stat.st_gid != expected_directory_gid
        or stat.S_IMODE(parent_stat.st_mode) != expected_directory_mode
    ):
        raise RuntimeError("model control socket directory is invalid")

    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISSOCK(existing.st_mode)
            or existing.st_uid != os.getuid()
            or existing.st_gid != os.getgid()
        ):
            raise RuntimeError("model control socket path is occupied")
        path.unlink()

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    created_identity: tuple[int, int] | None = None
    try:
        listener.bind(str(path))
        created_stat = path.lstat()
        created_identity = (created_stat.st_dev, created_stat.st_ino)
        os.chmod(path, SOCKET_MODE)
        listener.listen(socket.SOMAXCONN)
        final_stat = path.lstat()
        if (
            stat.S_ISLNK(final_stat.st_mode)
            or not stat.S_ISSOCK(final_stat.st_mode)
            or final_stat.st_uid != os.getuid()
            or final_stat.st_gid != os.getgid()
            or stat.S_IMODE(final_stat.st_mode) != SOCKET_MODE
        ):
            raise RuntimeError("model control socket permissions are invalid")
        listener.set_inheritable(True)
        return listener
    except BaseException:
        listener.close()
        if created_identity is not None:
            try:
                current = path.lstat()
                if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == created_identity:
                    path.unlink()
            except OSError:
                pass
        raise


def main() -> None:
    listener = bind_control_listener()
    os.execvp(
        "uvicorn",
        ["uvicorn", "scraper.llm_control:app", "--fd", str(listener.fileno())],
    )


if __name__ == "__main__":
    main()
