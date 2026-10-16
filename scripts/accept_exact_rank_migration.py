#!/usr/bin/env python3
"""Offline exact-rank migration acceptance; never changes serving configuration."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import re
import signal
import sys
import urllib.request
from pathlib import Path

TOLERANCE = 0.0001
COUNT = 100
LIMIT = 10


def validate_results(exact, projected, scores):
    """Compare unique top-ten lists using independently recomputed cosine scores."""
    lists = (exact, projected)
    for hits in lists:
        if len(hits) != LIMIT or len({key for key, _ in hits}) != LIMIT:
            raise ValueError("missing or duplicate results")
    union = {key for hits in lists for key, _ in hits}
    if set(scores) != union or not all(
        math.isfinite(value) for value in scores.values()
    ):
        raise ValueError("missing or nonfinite recomputed scores")
    for hits in lists:
        for key, value in hits:
            if not math.isfinite(value) or abs(value - scores[key]) > TOLERANCE:
                raise ValueError("score disagreement")
        for i, (left, _) in enumerate(hits):
            if any(
                scores[right] - scores[left] > TOLERANCE for right, _ in hits[i + 1 :]
            ):
                raise ValueError("material rank inversion")
    cutoff = min(scores[key] for key, _ in projected)
    difference = {key for key, _ in exact} ^ {key for key, _ in projected}
    if any(abs(scores[key] - cutoff) > TOLERANCE for key in difference):
        raise ValueError("material boundary difference")
    return exact == projected


def parse_metrics(body):
    families = {
        "groktocrawl_pgvector_shadow_operations_total": "write_failure",
        "groktocrawl_pgvector_shadow_comparisons_total": "comparison_failure",
    }
    declared = set()
    totals = dict.fromkeys(families.values(), 0.0)
    seen = set()
    for line in body.splitlines():
        for family in families:
            if line == f"# TYPE {family} counter":
                if family in declared:
                    raise ValueError("duplicate metric declaration")
                declared.add(family)
        if not any(line.startswith(family) for family in families):
            continue
        match = re.fullmatch(r"([^\s{]+)\{([^}]*)\}\s+([^\s]+)(?:\s+\d+)?", line)
        if not match:
            raise ValueError("malformed metric")
        name, labels, raw = match.groups()
        if name not in families:
            continue
        pairs = re.findall(r'(\w+)="([^"\n]*)"', labels)
        if ",".join(f'{key}="{value}"' for key, value in pairs) != labels or len(
            dict(pairs)
        ) != len(pairs):
            raise ValueError("malformed metric labels")
        identity = (name, tuple(sorted(pairs)))
        if identity in seen:
            raise ValueError("duplicate metric series")
        seen.add(identity)
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError("invalid metric")
        operation = dict(pairs)
        if operation.get("outcome") == "failure" and (
            name.endswith("comparisons_total")
            or operation.get("operation") in {"upsert", "delete"}
        ):
            totals[families[name]] += value
    if "groktocrawl_pgvector_shadow_operations_total" not in declared:
        raise ValueError("required operations counter declaration absent")
    if any(name not in declared for name, _ in seen):
        raise ValueError("series without counter declaration")
    totals["comparison_observed"] = (
        "groktocrawl_pgvector_shadow_comparisons_total" in declared
    )
    return totals


def metric_counts():
    with urllib.request.urlopen(
        "http://semantic-svc:8003/metrics", timeout=3
    ) as response:
        return parse_metrics(response.read(1_000_000).decode("utf-8", "strict"))


def _sample_ids(qdrant, collection: str, count: int) -> list[int]:
    # Keep the 100 IDs with the smallest stable SHA-256 rank. This makes the
    # query set deterministic while bounding memory to O(count), even if the
    # Qdrant collection grows substantially.
    best: list[tuple[int, int]] = []
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=collection,
            limit=1000,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        for point in points:
            point_id = int(point.id)
            rank = int.from_bytes(
                hashlib.sha256(str(point_id).encode()).digest(), "big"
            )
            item = (-rank, point_id)
            if len(best) < count:
                heapq.heappush(best, item)
            elif rank < -best[0][0]:
                heapq.heapreplace(best, item)
        if offset is None:
            break
    return [point_id for _, point_id in sorted(best, key=lambda item: -item[0])]


def _queries(
    qdrant, collection: str, model: str, ids: list[int]
) -> list[tuple[int, list[float]]]:
    result = []
    for start in range(0, len(ids), 100):
        points = qdrant.retrieve(
            collection_name=collection,
            ids=ids[start : start + 100],
            with_payload=False,
            with_vectors=[model],
        )
        for point in points:
            vectors = point.vector
            if not isinstance(vectors, dict) or model not in vectors:
                raise RuntimeError(
                    "sample vector missing from authoritative collection"
                )
            result.append((int(point.id), [float(value) for value in vectors[model]]))
    return sorted(result, key=lambda item: item[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sample-sha256", required=True)
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--tooling-revision", required=True)
    args = parser.parse_args()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", args.expected_sample_sha256)
        or not re.fullmatch(r"[0-9a-f]{40}", args.runtime_revision)
        or not re.fullmatch(r"[0-9a-f]{40}", args.tooling_revision)
    ):
        raise ValueError("invalid provenance arguments")
    signal.signal(
        signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("deadline"))
    )
    signal.alarm(900)
    from qdrant_client import QdrantClient, models
    from shadow_pgvector import PgvectorShadowStore, ShadowConfig, _vector_literal

    config = ShadowConfig.from_env()
    if (
        config.mode != "shadow_pgvector"
        or config.sample_rate != 1.0
        or config.score_tolerance != TOLERANCE
    ):
        raise ValueError("requires full shadow sampling and fixed tolerance")
    collection = os.getenv("QDRANT_COLLECTION", "groktocrawl_pages")
    model = os.getenv("ACTIVE_EMBED_MODEL", "v_bge-m3")
    if model != "v_bge-m3":
        raise ValueError("unsupported named vector")
    q = QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"), timeout=10)
    store = PgvectorShadowStore(config, int(os.getenv("EMBED_DIM", "1024")))
    try:
        before = metric_counts()
        selected = _sample_ids(q, collection, COUNT)
        digest = hashlib.sha256(",".join(map(str, selected)).encode()).hexdigest()
        if (
            len(selected) != COUNT
            or len(set(selected)) != COUNT
            or digest != args.expected_sample_sha256
        ):
            raise ValueError("sample mismatch")
        queries = _queries(q, collection, model, selected)
        if len(queries) != COUNT or {key for key, _ in queries} != set(selected):
            raise ValueError("query vector mismatch")
        strict = 0
        for _, vector in queries:
            if not vector or not all(math.isfinite(value) for value in vector):
                raise ValueError("invalid vector")
            hits = q.query_points(
                collection_name=collection,
                query=vector,
                using=model,
                limit=LIMIT,
                search_params=models.SearchParams(exact=True),
            ).points
            projected = store.search(vector, model=model, limit=LIMIT)
            exact = [(int(hit.id), float(hit.score)) for hit in hits]
            pg = [(hit.point_id, hit.score) for hit in projected]
            union = list({key for key, _ in exact + pg})
            rows = (
                store._connect()
                .execute(
                    f"SELECT point_id, 1-(embedding <=> %s::vector) FROM {config.schema}.{config.table} WHERE model=%s AND deleted=false AND point_id=ANY(%s)",
                    (_vector_literal(vector), model, union),
                )
                .fetchall()
            )
            scores = {int(row[0]): float(row[1]) for row in rows}
            validate_results(exact, pg, scores)
            strict += [key for key, _ in exact] == [key for key, _ in pg]
        after = metric_counts()
        if any(
            after[key] != before[key] for key in ("write_failure", "comparison_failure")
        ) or (before["comparison_observed"] and not after["comparison_observed"]):
            raise ValueError("error counters changed or reset")
        print(
            json.dumps(
                {
                    "passed": True,
                    "acceptance": "exact_rank_tie_aware",
                    "runtime_revision": args.runtime_revision,
                    "tooling_revision": args.tooling_revision,
                    "runner_sha256": hashlib.sha256(
                        Path(__file__).read_bytes()
                    ).hexdigest(),
                    "sample_sha256": digest,
                    "queries": COUNT,
                    "limit": LIMIT,
                    "tolerance": TOLERANCE,
                    "runtime_comparison_monitor": "observed"
                    if before["comparison_observed"] and after["comparison_observed"]
                    else "not_observed",
                    "ordered_id_matches": strict,
                    "tie_equivalent": COUNT - strict,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        q.close()
        store.close()
        signal.alarm(0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps({"passed": False, "error_type": type(error).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
