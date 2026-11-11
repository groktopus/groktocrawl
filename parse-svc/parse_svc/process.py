"""Bound subprocess wall time and both output streams before buffering them."""

import contextlib
import os
import selectors
import signal
import subprocess
import tempfile
import time


def bounded_run(
    args,
    *,
    timeout,
    input=None,
    pass_fds=(),
    output_limit=10 * 1024 * 1024,
    error_limit=64 * 1024,
):
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as source:
        if input:
            source.write(input)
        source.seek(0)
        proc = subprocess.Popen(
            args,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        streams = {}
        completed = False
        try:
            with selectors.DefaultSelector() as selector:
                for stream, limit in (
                    (proc.stdout, output_limit),
                    (proc.stderr, error_limit),
                ):
                    selector.register(stream, selectors.EVENT_READ, limit)
                    streams[stream] = bytearray()
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if len(streams[key.fileobj]) + len(chunk) > key.data:
                            raise subprocess.SubprocessError("subprocess_output_limit")
                        streams[key.fileobj].extend(chunk)
            proc.wait(timeout=max(0.001, deadline - time.monotonic()))
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, args)
            completed = True
            return subprocess.CompletedProcess(
                args,
                proc.returncode,
                bytes(streams[proc.stdout]),
                bytes(streams[proc.stderr]),
            )
        finally:
            if not completed:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()
