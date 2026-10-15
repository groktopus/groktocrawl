import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8003"
SEED = "groktocrawl-x-pgvector-cutover-v1"


def request(method, path, payload=None, expected=None):
    body = None if payload is None else json.dumps(payload, sort_keys=True).encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    if expected is not None and status not in expected:
        raise RuntimeError(f"{method} {path}: expected {expected}, got {status}: {raw[:300]!r}")
    parsed = json.loads(raw) if raw else None
    return {"status": status, "elapsed_ms": elapsed_ms, "body": parsed,
            "digest": hashlib.sha256(raw).hexdigest()}


def normalized_search(body):
    return [
        {"url": item["url"], "title": item["title"], "score": round(item["score"], 5)}
        for item in body["results"]
    ]


phase = sys.argv[1]
if phase == "health":
    print(json.dumps(request("GET", "/health", expected={200, 503}), sort_keys=True))
    raise SystemExit()

records = []
for index in range(6):
    token = hashlib.sha256(f"{SEED}:{index}".encode()).hexdigest()[:16]
    records.append({
        "url": f"https://pilot.invalid/cutover/{token}",
        "title": f"Cutover sample {index}",
        "content": f"Synthetic enterprise agent factory cutover evidence {token} with durable orchestration, governance, and retained artifacts.",
    })

if phase == "seed":
    outputs = [request("POST", "/index", records[0], {201})]
    outputs.append(request("POST", "/index/batch", {"pages": records[1:]}, {201}))
    search = request("POST", "/search/vector", {"query": records[0]["content"], "limit": 6}, {200})
    search["normalized"] = normalized_search(search["body"])
    outputs.extend([search, request("GET", "/index/stats", expected={200}), request("GET", "/index/model", expected={200})])
    print(json.dumps({"phase": phase, "records": records, "outputs": outputs}, indent=2, sort_keys=True))
elif phase == "read":
    search = request("POST", "/search/vector", {"query": records[0]["content"], "limit": 6}, {200})
    search["normalized"] = normalized_search(search["body"])
    print(json.dumps({"phase": phase, "search": search, "stats": request("GET", "/index/stats", expected={200}), "model": request("GET", "/index/model", expected={200})}, indent=2, sort_keys=True))
elif phase == "mutate":
    fresh = {"url": records[0]["url"] + "/fresh", "title": "Fresh cutover write", "content": records[0]["content"] + " fresh write"}
    created = request("POST", "/index", fresh, {201})
    found = request("POST", "/search/vector", {"query": fresh["content"], "limit": 3}, {200})
    deleted = request("DELETE", f"/index/{created['body']['url_hash']}", expected={200})
    after = request("POST", "/search/vector", {"query": fresh["content"], "limit": 10}, {200})
    print(json.dumps({"phase": phase, "fresh": fresh, "created": created, "found": normalized_search(found["body"]), "deleted": deleted, "after": normalized_search(after["body"])}, indent=2, sort_keys=True))
elif phase == "write_failure":
    item = {"url": records[0]["url"] + "/qdrant-down", "title": "Rejected rollback-gap write", "content": records[0]["content"] + " qdrant down"}
    print(json.dumps({"phase": phase, "response": request("POST", "/index", item, {503})}, indent=2, sort_keys=True))
else:
    raise SystemExit(f"unknown phase: {phase}")
