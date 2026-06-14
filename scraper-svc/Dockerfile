FROM python:3.13-slim

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libatspi2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY scraper-svc/pyproject.toml .

# Install with playwright extras for Tier 3
RUN pip install --no-cache-dir -e ".[playwright]"
RUN python -m playwright install chromium

COPY scraper-svc/scraper/ scraper/
COPY common/ common/

EXPOSE 8001
CMD ["uvicorn", "scraper.app:app", "--host", "0.0.0.0", "--port", "8001"]
