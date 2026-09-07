FROM python:3.13-slim

WORKDIR /app
COPY semantic-svc/pyproject.toml .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -e .

COPY semantic-svc/app.py semantic-svc/auth.py semantic-svc/inference.py semantic-svc/models.py semantic-svc/retention.py semantic-svc/router_index.py semantic-svc/router_search.py semantic-svc/router_migration.py semantic-svc/metrics.py ./
COPY common/ common/

EXPOSE 8003
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
