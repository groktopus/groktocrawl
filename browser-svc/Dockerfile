FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libatspi2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY browser-svc/pyproject.toml .
RUN pip install --no-cache-dir -e .
RUN python -m playwright install chromium

COPY browser-svc/browser_svc/ browser_svc/
COPY common/ common/

EXPOSE 8012
CMD ["uvicorn", "browser_svc.app:app", "--host", "0.0.0.0", "--port", "8012"]
