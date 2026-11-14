FROM python:3.12-slim

WORKDIR /app

# Install pinned dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    mcp==1.27.0 \
    httpx==0.28.1 \
    pydantic==2.11.4 \
    "uvicorn[standard]==0.34.3"

# Copy application code
COPY groktocrawl_client.py .
COPY browser_handler.py .
COPY mcp_server.py .
COPY pagination.py .
COPY session_store.py .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1

EXPOSE 8002
CMD ["python", "mcp_server.py"]
