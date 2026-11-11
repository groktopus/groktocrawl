FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr tesseract-ocr-eng \
    libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY parse-svc/pyproject.toml .
RUN pip install --no-cache-dir -e .


# Optional table extraction; never blocks baseline OCR installation.
RUN pip install --no-cache-dir camelot-py || true

COPY parse-svc/parse_svc/ parse_svc/
COPY common/ common/

EXPOSE 8013
CMD ["uvicorn", "parse_svc.app:app", "--host", "0.0.0.0", "--port", "8013"]
