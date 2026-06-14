FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY agent/ agent/
COPY common/ common/

CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
