"""Apply narrow, fail-closed patches to the pinned FlareSolverr image."""

from __future__ import annotations

from pathlib import Path

SOURCE = Path("/app/flaresolverr.py")
UTILS = Path("/app/utils.py")


def replace_once(source: str, old: str, new: str, *, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"pinned FlareSolverr source drift: {label}")
    return source.replace(old, new, 1)


def patch(source: str) -> str:
    source = replace_once(
        source,
        "req = V1RequestBase(data)",
        """# Protected capture ignores all caller and session proxy overrides.
    command = data.get("cmd")
    if command in {"request.get", "request.post"}:
        from urllib.parse import urlsplit
        target = data.get("url")
        try:
            parsed_target = urlsplit(target)
            parsed_target.port
        except (TypeError, ValueError):
            raise ValueError("request URL is invalid") from None
        if (
            parsed_target.scheme not in {"http", "https"}
            or not parsed_target.hostname
            or parsed_target.username is not None
            or parsed_target.password is not None
        ):
            raise ValueError("request URL is not an allowed HTTP destination")
    data["proxy"] = {"url": "http://172.31.254.18:8080"}
    req = V1RequestBase(data)""",
        label="force-fixed-proxy-and-http-url",
    )
    source = replace_once(
        source,
        "serve(handler, host=self.host, port=self.port, asyncore_use_poll=True)",
        """socket_path = os.environ.get("FLARE_API_UNIX_SOCKET", "")
            if not socket_path or not os.path.isabs(socket_path):
                raise RuntimeError("protected API socket path is invalid")
            socket_dir = os.path.dirname(socket_path)
            os.makedirs(socket_dir, mode=0o770, exist_ok=True)
            if os.path.lexists(socket_path):
                if os.path.islink(socket_path) or not os.path.exists(socket_path):
                    raise RuntimeError("protected API socket path is unsafe")
                import stat
                if not stat.S_ISSOCK(os.stat(socket_path, follow_symlinks=False).st_mode):
                    raise RuntimeError("protected API socket path is not a socket")
                os.unlink(socket_path)
            serve(
                handler,
                unix_socket=socket_path,
                unix_socket_perms="660",
                asyncore_use_poll=True,
            )""",
        label="unix-only-api-listener",
    )
    return source


def patch_utils(source: str) -> str:
    return replace_once(
        source,
        "options.add_argument('--proxy-server=%s' % proxy_url)",
        """options.add_argument('--proxy-server=%s' % proxy_url)
    # Chromium implicitly bypasses configured proxies for loopback addresses.
    # Remove that exception; direct loopback is separately isolated from API.
    options.add_argument('--proxy-bypass-list=<-loopback>')
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-component-update')
    options.add_argument('--disable-default-apps')
    options.add_argument('--no-first-run')""",
        label="disable-chromium-loopback-proxy-bypass",
    )


def main() -> None:
    original = SOURCE.read_text(encoding="utf-8")
    updated = patch(original)
    SOURCE.write_text(updated, encoding="utf-8")
    utils = UTILS.read_text(encoding="utf-8")
    UTILS.write_text(patch_utils(utils), encoding="utf-8")


if __name__ == "__main__":
    main()
