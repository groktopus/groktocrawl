"""Killable pypdf boundary. One inspection or one physical page per invocation."""

import json
import sys

from pypdf import PdfReader


def main():
    reader = PdfReader(sys.argv[1])
    if reader.is_encrypted:
        raise ValueError("encrypted_pdf")
    page = int(sys.argv[2])
    if page == 0:
        result = {"pages": len(reader.pages)}
    else:
        text = reader.pages[page - 1].extract_text() or ""
        if len(text.encode()) > 10 * 1024 * 1024:
            raise ValueError("native_page_output_limit")
        result = {"text": text}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
