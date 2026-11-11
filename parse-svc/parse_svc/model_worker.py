"""Killable local model HTTP request; remote inference uncertainty belongs to parent."""

import json
import sys

import httpx

from .vision_ocr import OcrSettings, headers


def main():
    settings = OcrSettings.load()
    settings.validate_model()
    payload = json.load(sys.stdin)
    with httpx.Client(
        timeout=settings.page_timeout, follow_redirects=False, trust_env=False
    ) as client:
        with client.stream(
            "POST",
            settings.base_url + "/chat/completions",
            json=payload,
            headers=headers(),
        ) as response:
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 2 * 1024 * 1024:
                    raise ValueError("model_response_limit")
            sys.stdout.write(
                json.dumps(
                    {"status": response.status_code, "body": chunks.decode("utf-8")}
                )
            )


if __name__ == "__main__":
    main()
