"""Real semantic-svc HTTP acceptance with PostgreSQL and no Qdrant service."""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.outcome_governance import governed_skip


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _server_launcher(path: Path) -> None:
    path.write_text(
        """
import hashlib
import os

import numpy as np
import app as semantic_app


class DeterministicSentenceTransformer:
    def __init__(self, model_name):
        self.model_name = model_name

    def encode(self, inputs, normalize_embeddings=True):
        del normalize_embeddings
        single = isinstance(inputs, str)
        texts = [inputs] if single else list(inputs)
        vectors = []
        for text in texts:
            raw = hashlib.shake_256(text.encode()).digest(
                4 * semantic_app.EMBED_DIM
            )
            vector = np.frombuffer(raw, dtype=np.uint32).astype(np.float32)
            vector = vector / np.float32(2**31) - 1.0
            vector /= np.linalg.norm(vector)
            vectors.append(vector)
        result = vectors[0] if single else np.stack(vectors)
        return result


class DeterministicCrossEncoder:
    def __init__(self, model_name):
        self.model_name = model_name

    def predict(self, pairs):
        return np.zeros(len(pairs), dtype=np.float32)


semantic_app._load_sentence_transformers = lambda: (
    DeterministicCrossEncoder,
    DeterministicSentenceTransformer,
)

import uvicorn

uvicorn.run(
    semantic_app.app,
    host="127.0.0.1",
    port=int(os.environ["SEMANTIC_TEST_PORT"]),
    log_level="warning",
)
""",
        encoding="utf-8",
    )


@contextlib.contextmanager
def _semantic_server(
    *, launcher: Path, repo_root: Path, log_path: Path, dsn: str, active_model: str
) -> Iterator[str]:
    port = _free_port()
    env = os.environ.copy()
    semantic_path = str(repo_root / "semantic-svc")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (
            semantic_path,
            str(repo_root / "agent-svc"),
            str(repo_root),
            existing_pythonpath,
        )
        if item
    )
    env.update(
        {
            "VECTOR_STORE_MODE": "pgvector_only",
            "PGVECTOR_SHADOW_DSN": dsn,
            # This address is deliberately unreachable. The private Docker
            # network also prevents any accidental public service access.
            "QDRANT_URL": "http://127.0.0.1:1",
            "EMBED_DIM": "1024",
            "ACTIVE_EMBED_MODEL": active_model,
            "SEMANTIC_TEST_PORT": str(port),
        }
    )

    with log_path.open("ab") as server_log:
        process = subprocess.Popen(
            [sys.executable, str(launcher)],
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            last_error: Exception | None = None
            ready = False
            with httpx.Client(timeout=2.0) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        response = client.get(f"{base_url}/ready")
                        if response.status_code == 200:
                            ready = True
                            break
                    except httpx.HTTPError as exc:
                        last_error = exc
                    time.sleep(0.2)

            if process.poll() is not None:
                log_path_text = log_path.read_text(encoding="utf-8", errors="replace")
                raise AssertionError(
                    "semantic-svc subprocess exited before becoming ready; "
                    f"logs={log_path_text[-4000:]}"
                )
            if not ready:
                raise AssertionError(
                    "semantic-svc did not become ready within 30 seconds; "
                    f"last_error={last_error}; "
                    f"logs={log_path.read_text(encoding='utf-8', errors='replace')[-4000:]}"
                )
            yield base_url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _delete_test_rows(dsn: str, urls: list[str]) -> None:
    if not urls:
        return
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "DELETE FROM groktocrawl_x_semantic_shadow.pages WHERE url = ANY(%s)",
            (urls,),
        )


@pytest.mark.enable_socket
def test_semantic_pgvector_only_http_without_qdrant(tmp_path: Path) -> None:
    """Exercise service startup, indexing, search, stats, delete, and restart."""
    dsn = os.getenv("PGVECTOR_SEMANTIC_TEST_DSN", "").strip()
    if not dsn:
        governed_skip(
            "set PGVECTOR_SEMANTIC_TEST_DSN to an isolated test database",
            owner="repository-maintainer",
            issue="#672",
            classification="retained",
            environment="opt-in PostgreSQL integration DSN is not configured",
        )

    repo_root = Path(__file__).resolve().parents[2]
    launcher = tmp_path / "semantic_http_server.py"
    server_log = tmp_path / "semantic_http_server.log"
    _server_launcher(launcher)

    run_id = uuid.uuid4().hex
    active_model = f"pgvector-http-{run_id}"
    single_url = f"https://example.test/pgvector-http/{run_id}/single"
    batch_urls = [
        f"https://example.test/pgvector-http/{run_id}/batch-a",
        f"https://example.test/pgvector-http/{run_id}/batch-b",
    ]
    single_content = f"pgvector-http-{run_id} single seeded semantic document"
    batch_contents = [
        f"pgvector-http-{run_id} batch alpha document",
        f"pgvector-http-{run_id} batch beta document",
    ]
    urls = [single_url, *batch_urls]
    try:
        with (
            _semantic_server(
                launcher=launcher,
                repo_root=repo_root,
                log_path=server_log,
                dsn=dsn,
                active_model=active_model,
            ) as base_url,
            httpx.Client(base_url=base_url, timeout=10.0) as client,
        ):
            health = client.get("/health")
            assert health.status_code == 200, health.text
            assert health.json() == {
                "status": "ok",
                "models": "loaded",
                "vector_store": "pgvector",
                "pgvector": "ready",
                "qdrant": "not_required",
            }
            assert client.get("/ready").status_code == 200

            single = client.post(
                "/index",
                json={"url": single_url, "title": "single", "content": single_content},
            )
            assert single.status_code == 201, single.text
            single_id = single.json()["url_hash"]

            batch = client.post(
                "/index/batch",
                json={
                    "pages": [
                        {
                            "url": batch_urls[index],
                            "title": f"batch-{index}",
                            "content": batch_contents[index],
                        }
                        for index in range(2)
                    ]
                },
            )
            assert batch.status_code == 201, batch.text
            assert batch.json() == {"status": "indexed", "count": 2}

            search = client.post(
                "/search/vector", json={"query": single_content, "limit": 3}
            )
            assert search.status_code == 200, search.text
            results = search.json()["results"]
            assert results and results[0]["url"] == single_url
            assert results[0]["score"] == pytest.approx(1.0, abs=1e-6)

            stats = client.get("/index/stats")
            assert stats.status_code == 200, stats.text
            assert stats.json()["total_docs"] == 3

            deleted = client.delete(f"/index/{single_id}")
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"status": "deleted"}
            assert client.get("/index/stats").json()["total_docs"] == 2

        # A new process proves the persisted pgvector projection serves search.
        with (
            _semantic_server(
                launcher=launcher,
                repo_root=repo_root,
                log_path=server_log,
                dsn=dsn,
                active_model=active_model,
            ) as base_url,
            httpx.Client(base_url=base_url, timeout=10.0) as client,
        ):
            health = client.get("/health")
            assert health.status_code == 200, health.text
            assert health.json()["qdrant"] == "not_required"
            search = client.post(
                "/search/vector", json={"query": batch_contents[0], "limit": 3}
            )
            assert search.status_code == 200, search.text
            assert search.json()["results"][0]["url"] == batch_urls[0]
            assert client.get("/index/stats").json()["total_docs"] == 2
            for url in batch_urls:
                url_hash = int(hashlib.sha256(url.encode()).hexdigest()[:16], 16)
                response = client.delete(f"/index/{url_hash}")
                assert response.status_code == 200, response.text
            assert client.get("/index/stats").json()["total_docs"] == 0
    finally:
        with contextlib.suppress(Exception):
            _delete_test_rows(dsn, urls)
