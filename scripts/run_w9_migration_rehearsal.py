#!/usr/bin/env python3
"""Run the frozen W9 PostgreSQL/pgvector cutover and Qdrant rollback rehearsal."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

MODEL = "v_bge-m3"
CORPUS = [
    {
        "url": f"https://w9-{index}.invalid/evidence",
        "title": f"W9 evidence {index}",
        "content": f"bounded migration evidence document {index} "
        + "evidence " * index,
    }
    for index in range(1, 7)
]
PINNED_QUERY = "bounded migration evidence"


class RehearsalError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> str:
    result = subprocess.run(
        command,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1:] or ["command failed"]
        raise RehearsalError(detail[0])
    return result.stdout.strip()


class Candidate:
    def __init__(self, compose_file: str, env_file: str) -> None:
        self.base = ["docker", "compose", "--env-file", env_file, "-f", compose_file]

    def compose(
        self,
        *args: str,
        mode: str | None = None,
        stdin: str | None = None,
    ) -> str:
        env = os.environ.copy()
        if mode:
            env["VECTOR_STORE_MODE"] = mode
        return _run([*self.base, *args], env=env, stdin=stdin)

    def python(self, service: str, program: str) -> Any:
        raw = self.compose("exec", "-T", service, "python3", "-c", program)
        return json.loads(raw)

    def psql_json(self, database: str, sql: str) -> dict[str, Any]:
        raw = self.compose(
            "exec",
            "-T",
            "candidate-postgres",
            "psql",
            "-XAt",
            "-U",
            "groktocrawl_x",
            "-d",
            database,
            "-c",
            sql,
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RehearsalError("PostgreSQL manifest was not an object")
        return parsed


POSTGRES_MANIFEST_SQL = r"""
WITH
v AS (SELECT version FROM research_staging.schema_version),
sets AS (
 SELECT count(*)::int total,
   count(*) FILTER (WHERE NOT deleted)::int active,
   count(*) FILTER (WHERE deleted)::int deleted,
   encode(sha256(convert_to(coalesce(string_agg(scope_id::text||':'||research_id::text||':'||deleted::text||':'||set_digest,',' ORDER BY scope_id,research_id),''),'UTF8')),'hex') digest
 FROM research_staging.research_artifact_sets
), arts AS (
 SELECT count(*)::int total,
   encode(sha256(convert_to(coalesce(string_agg(scope_id::text||':'||research_id::text||':'||artifact_id||':'||content_digest,',' ORDER BY scope_id,research_id,artifact_id),''),'UTF8')),'hex') digest
 FROM research_staging.research_artifacts
), roots AS (
 SELECT count(*)::int total, count(*) FILTER (WHERE deleted)::int deleted,
   encode(sha256(convert_to(coalesce(string_agg(scope_id::text||':'||root_id||':'||deleted::text,',' ORDER BY scope_id,root_id),''),'UTF8')),'hex') digest
 FROM research_staging.roots
), vec AS (
 SELECT count(*) FILTER (WHERE NOT deleted)::int active,
   count(*) FILTER (WHERE deleted)::int deleted,
   encode(sha256(convert_to(coalesce(string_agg(point_id::text||':'||model||':'||deleted::text||':'||encode(sha256(convert_to(payload::text,'UTF8')),'hex'),',' ORDER BY point_id),''),'UTF8')),'hex') digest
 FROM groktocrawl_x_semantic_shadow.pages
)
SELECT json_build_object(
 'schema_version',(SELECT version FROM v),
 'artifact_sets',(SELECT row_to_json(sets) FROM sets),
 'artifacts',(SELECT row_to_json(arts) FROM arts),
 'retention_roots',(SELECT row_to_json(roots) FROM roots),
 'vectors',(SELECT row_to_json(vec) FROM vec),
 'vector_model','v_bge-m3',
 'claims_and_evidence','covered_by_artifact_content_digests'
);
"""


SEMANTIC_STATE_PROGRAM = r"""
import hashlib,json,os,urllib.request
from qdrant_client import QdrantClient
def call(path,method='GET',body=None):
 data=None if body is None else json.dumps(body).encode()
 req=urllib.request.Request('http://127.0.0.1:8003'+path,data=data,method=method,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=180) as r:return json.load(r)
q=QdrantClient(url=os.environ['QDRANT_URL'])
ids=[]; offset=None
try:
 while True:
  points,offset=q.scroll(collection_name='groktocrawl_pages',limit=256,offset=offset,with_payload=False,with_vectors=False)
  ids += [int(x.id) for x in points]
  if offset is None: break
except Exception:
 ids=[]
health=call('/health')
model=call('/index/model')
search=call('/search/vector','POST',{'query':'bounded migration evidence','limit':6})
provider=health.get('vector_store') or ('qdrant' if health.get('qdrant') == 'ready' else None)
print(json.dumps({'health':health.get('status'),'vector_store':provider,'model':model.get('active_named_vector'),'served_count':model.get('total_docs'),'qdrant_count':len(ids),'qdrant_id_digest':hashlib.sha256(','.join(map(str,sorted(ids))).encode()).hexdigest(),'search_urls':[x['url'] for x in search.get('results',[])]},sort_keys=True))
q.close()
"""


def _semantic_write_program(documents: list[dict[str, str]]) -> str:
    payload = json.dumps(documents, separators=(",", ":"))
    return f"""
import json,urllib.request
docs=json.loads({payload!r}); out=[]
for doc in docs:
 req=urllib.request.Request('http://127.0.0.1:8003/index',data=json.dumps(doc).encode(),method='POST',headers={{'Content-Type':'application/json'}})
 with urllib.request.urlopen(req,timeout=180) as r: out.append(json.load(r))
print(json.dumps(out))
"""


def _semantic_delete_program(point_id: int) -> str:
    return f"""
import json,urllib.request
req=urllib.request.Request('http://127.0.0.1:8003/index/{point_id}',method='DELETE')
with urllib.request.urlopen(req,timeout=60) as r: print(json.dumps(json.load(r)))
"""


def _wait_ready(candidate: Candidate, mode: str, timeout: float = 120) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            state = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
            if state["health"] == "ok" and state["vector_store"] == mode:
                return round((time.monotonic() - started) * 1000, 3)
        except (RehearsalError, json.JSONDecodeError):
            pass
        time.sleep(2)
    raise RehearsalError(f"semantic service did not become ready in {mode} mode")


def _assert_equal(left: Any, right: Any, label: str) -> None:
    if left != right:
        raise RehearsalError(f"{label} did not reconcile")


def run(args: argparse.Namespace) -> dict[str, Any]:
    candidate = Candidate(args.compose_file, args.env_file)
    receipt: dict[str, Any] = {
        "schema_version": "w9-migration-rollback/1",
        "status": "running",
        "git_revision": _run(["git", "rev-parse", "HEAD"]),
        "corpus_size": len(CORPUS),
        "deleted_documents": 1,
        "rollback_target_ms": 120_000,
    }
    try:
        initial = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
        if initial["health"] != "ok" or initial["vector_store"] != "pgvector":
            raise RehearsalError("candidate did not begin healthy in pgvector mode")

        writes = candidate.python("candidate-semantic", _semantic_write_program(CORPUS))
        if len(writes) != len(CORPUS):
            raise RehearsalError("not all corpus writes completed")
        updated = dict(CORPUS[0])
        updated["content"] += " updated"
        candidate.python("candidate-semantic", _semantic_write_program([updated]))

        before_delete = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
        delete_id = int(writes[-1]["url_hash"])
        candidate.python("candidate-semantic", _semantic_delete_program(delete_id))
        after_delete = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
        if CORPUS[-1]["url"] in after_delete["search_urls"]:
            raise RehearsalError("deleted document remained in pgvector search")

        candidate.compose("restart", "candidate-semantic")
        restart_ms = _wait_ready(candidate, "pgvector")
        after_restart = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
        _assert_equal(
            after_delete["search_urls"], after_restart["search_urls"], "restart query"
        )

        source_manifest = candidate.psql_json("groktocrawl_x", POSTGRES_MANIFEST_SQL)
        backup_name = f"/tmp/w9-{time.time_ns()}.dump"
        restore_db = f"w9_restore_{time.time_ns()}"
        candidate.compose(
            "exec",
            "-T",
            "candidate-postgres",
            "pg_dump",
            "-Fc",
            "-U",
            "groktocrawl_x",
            "-d",
            "groktocrawl_x",
            "-f",
            backup_name,
        )
        candidate.compose(
            "exec",
            "-T",
            "candidate-postgres",
            "createdb",
            "-U",
            "groktocrawl_x",
            restore_db,
        )
        candidate.compose(
            "exec",
            "-T",
            "candidate-postgres",
            "pg_restore",
            "-U",
            "groktocrawl_x",
            "-d",
            restore_db,
            "--exit-on-error",
            backup_name,
        )
        restored_manifest = candidate.psql_json(restore_db, POSTGRES_MANIFEST_SQL)
        _assert_equal(source_manifest, restored_manifest, "PostgreSQL restore")
        candidate.compose(
            "exec",
            "-T",
            "candidate-postgres",
            "dropdb",
            "-U",
            "groktocrawl_x",
            restore_db,
        )
        candidate.compose("exec", "-T", "candidate-postgres", "rm", "-f", backup_name)

        candidate.compose(
            "up", "-d", "--force-recreate", "candidate-semantic", mode="qdrant"
        )
        rollback_ms = _wait_ready(candidate, "qdrant")
        rollback = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
        _assert_equal(
            after_restart["search_urls"], rollback["search_urls"], "rollback query"
        )
        _assert_equal(
            after_restart["served_count"], rollback["served_count"], "rollback count"
        )

        candidate.compose(
            "up", "-d", "--force-recreate", "candidate-semantic", mode="pgvector"
        )
        return_ms = _wait_ready(candidate, "pgvector")
        final = candidate.python("candidate-semantic", SEMANTIC_STATE_PROGRAM)
        _assert_equal(
            after_restart["search_urls"], final["search_urls"], "return query"
        )
        _assert_equal(final["qdrant_count"], final["served_count"], "provider count")
        if rollback_ms > 120_000:
            raise RehearsalError("rollback exceeded target")

        receipt.update(
            status="passed",
            initial=initial,
            before_delete=before_delete,
            after_delete=after_delete,
            after_restart=after_restart,
            rollback=rollback,
            final=final,
            postgres_manifest=source_manifest,
            restored_manifest_match=True,
            restart_ready_ms=restart_ms,
            rollback_ready_ms=rollback_ms,
            return_to_pgvector_ready_ms=return_ms,
            zero_unexplained_differences=True,
        )
        return receipt
    except Exception as exc:
        receipt.update(
            status="failed", failure_type=type(exc).__name__, failure=str(exc)
        )
        try:
            candidate.compose(
                "up", "-d", "--force-recreate", "candidate-semantic", mode="pgvector"
            )
        except Exception:
            receipt["pgvector_restore_failed"] = True
        return receipt


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--env-file", required=True)
    value.add_argument("--compose-file", default="compose.experimental-candidate.yml")
    value.add_argument("--output", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    receipt = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
