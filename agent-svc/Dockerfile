FROM python:3.13-slim

ARG GROKTOCRAWL_REVISION=unknown
ENV GROKTOCRAWL_REVISION=${GROKTOCRAWL_REVISION}

WORKDIR /app
COPY agent-svc/pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY agent-svc/agent/ agent/
COPY common/ common/
COPY scripts/reconcile-jobs.py scripts/reconcile-jobs.py

CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
