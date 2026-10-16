FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends iptables util-linux tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY scraper-svc/pyproject.toml .
RUN pip install --no-cache-dir -e .

# Install with playwright extras for Tier 3
RUN pip install --no-cache-dir -e ".[playwright]"
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium
RUN chmod -R a+rX /ms-playwright \
    && mkdir -p /home/scraper \
    && chown 10001:20000 /home/scraper

COPY scraper-svc/scraper/ scraper/
COPY common/ common/

# The CloakBrowser binary is downloaded at runtime into the mounted cache.
COPY scraper-svc/docker-entrypoint.sh /usr/local/bin/scraper-entrypoint
RUN chmod +x /usr/local/bin/scraper-entrypoint

EXPOSE 8001
CMD ["scraper-entrypoint"]
