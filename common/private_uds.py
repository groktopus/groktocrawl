"""Create permission-checked Unix listeners for protected internal APIs."""

from __future__ import annotations

import os
import socket
import stat
from pathlib import Path


def bind_private_listener(
    path: str | os.PathLike[str],
    *,
    directory_uid: int,
    directory_gid: int,
    directory_mode: int = 0o2710,
    socket_uid: int | None = None,
    socket_gid: int = 20000,
    socket_mode: int = 0o660,
) -> socket.socket:
    """Bind a listener only inside the expected directory and verify its inode."""
    socket_path = Path(path)
    if not socket_path.is_absolute() or "\x00" in str(path):
        raise RuntimeError("private socket path must be absolute")
    uid = os.getuid() if socket_uid is None else socket_uid
    if os.getuid() != uid:
        raise RuntimeError("private socket process identity is invalid")
    parent = socket_path.parent.lstat()
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != directory_uid
        or parent.st_gid != directory_gid
        or stat.S_IMODE(parent.st_mode) != directory_mode
    ):
        raise RuntimeError("private socket directory is invalid")

    try:
        existing = socket_path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISSOCK(existing.st_mode)
            or existing.st_uid != uid
            or existing.st_gid != socket_gid
            or stat.S_IMODE(existing.st_mode) != socket_mode
        ):
            raise RuntimeError("private socket path is occupied")
        socket_path.unlink()

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    created_identity: tuple[int, int] | None = None
    try:
        listener.bind(str(socket_path))
        created = socket_path.lstat()
        created_identity = (created.st_dev, created.st_ino)
        if created.st_gid != socket_gid:
            os.chown(socket_path, uid, socket_gid)
        os.chmod(socket_path, socket_mode)
        listener.listen(socket.SOMAXCONN)
        final = socket_path.lstat()
        if (
            stat.S_ISLNK(final.st_mode)
            or not stat.S_ISSOCK(final.st_mode)
            or final.st_uid != uid
            or final.st_gid != socket_gid
            or stat.S_IMODE(final.st_mode) != socket_mode
        ):
            raise RuntimeError("private socket permissions are invalid")
        listener.set_inheritable(True)
        return listener
    except BaseException:
        listener.close()
        if created_identity is not None:
            try:
                current = socket_path.lstat()
                if (
                    stat.S_ISSOCK(current.st_mode)
                    and (current.st_dev, current.st_ino) == created_identity
                ):
                    socket_path.unlink()
            except OSError:
                pass
        raise
