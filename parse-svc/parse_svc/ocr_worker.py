"""CPU OCR supervisor: owns the flock until its Tesseract child is reaped."""

import csv
import fcntl
import io
import json
import sys
import time
from pathlib import Path

from .process import bounded_run


def main():
    lock_path, deadline_raw = sys.argv[1:]
    deadline = float(deadline_raw)
    image = sys.stdin.buffer.read()
    with Path(lock_path).open("a+b") as lock:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("ocr_admission_timeout")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.02)
        # The actual child also inherits the descriptor, so even supervisor
        # termination cannot admit another OCR while this child remains alive.
        result = bounded_run(
            ["tesseract", "stdin", "stdout", "-l", "eng", "tsv"],
            input=image,
            timeout=max(0.001, deadline - time.monotonic()),
            pass_fds=(lock.fileno(),),
        )
        rows = csv.DictReader(io.StringIO(result.stdout.decode()), delimiter="\t")
        lines = {}
        confidence = []
        for row in rows:
            word = row.get("text", "").strip()
            if not word:
                continue
            key = tuple(
                row.get(field, "0") for field in ("block_num", "par_num", "line_num")
            )
            lines.setdefault(key, []).append(word)
            try:
                value = float(row["conf"])
                if 0 <= value <= 100:
                    confidence.append(value / 100)
            except (ValueError, KeyError):
                pass
        output = [
            "\n".join(" ".join(words) for words in lines.values()),
            sum(confidence) / len(confidence) if confidence else None,
            len(confidence),
        ]
        sys.stdout.write(json.dumps(output))
        # Closing (rather than explicitly unlocking) preserves inherited locks.


if __name__ == "__main__":
    main()
