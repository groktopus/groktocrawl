FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -e .

# Pre-download embedding model at build time so first request is fast
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

COPY app.py models.py retention.py router_index.py router_search.py router_migration.py metrics.py ./

EXPOSE 8003
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
