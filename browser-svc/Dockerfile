FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iptables passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY browser-svc/pyproject.toml .
RUN pip install --no-cache-dir -e .
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin browser \
    && chown -R 10001:10001 /ms-playwright

COPY browser-svc/browser_svc/ browser_svc/
COPY common/ common/

EXPOSE 8012
CMD ["uvicorn", "browser_svc.app:app", "--host", "0.0.0.0", "--port", "8012"]
