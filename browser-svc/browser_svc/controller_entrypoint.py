"""Pre-bind the protected controller UDS before its FastAPI startup hooks."""

from __future__ import annotations

import os

from common.private_uds import bind_private_listener

DEFAULT_SOCKET = "/run/browser-control/controller.sock"


def main() -> None:
    path = os.environ.get("BROWSER_CONTROLLER_SOCKET", DEFAULT_SOCKET)
    listener = bind_private_listener(
        path,
        directory_uid=0,
        directory_gid=20000,
        directory_mode=0o2710,
        socket_uid=0,
        socket_gid=20000,
    )
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "browser_svc.controller:app",
            "--fd",
            str(listener.fileno()),
        ],
    )


if __name__ == "__main__":
    main()
