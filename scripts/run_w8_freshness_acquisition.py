#!/usr/bin/env python3
"""Compare snippet-trusting and exact-fetch acquisition on a frozen W8 packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

SCHEMA = "enterprise-evaluation/w8-freshness-acquisition/1"


def digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value is not None else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, _newurl):
        del req, fp, code, msg, headers, _newurl
        return None


class Fixture:
    def __init__(self, packet: dict[str, Any]):
        self.cases = {case["case_id"]: case for case in packet["cases"]}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parts = self.path.split("?", 1)[0].strip("/").split("/")
                if len(parts) != 3 or parts[0] != "case":
                    self.send_error(404)
                    return
                case_id, raw_index = parts[1:]
                case = fixture.cases.get(case_id)
                try:
                    index = int(raw_index)
                    hop = case["fetch_chain"][index] if case else None
                except (ValueError, IndexError):
                    hop = None
                if hop is None:
                    self.send_error(404)
                    return
                self.send_response(hop["status"])
                self.send_header("X-Original-URL", hop["request_url"])
                self.send_header("X-Checked-At", hop["checked_at"])
                if hop["location"] is not None:
                    self.send_header("X-Original-Location", hop["location"])
                    self.send_header("Location", f"/case/{case_id}/{index + 1}")
                if hop["canonical_url"] is not None:
                    self.send_header("Link", f'<{hop["canonical_url"]}>; rel="canonical"')
                if hop["published_at"] is not None:
                    self.send_header("X-Published-At", hop["published_at"])
                if hop["modified_at"] is not None:
                    self.send_header("X-Modified-At", hop["modified_at"])
                body = (hop["body_utf8"] or "").encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Fixture:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def url(self, case_id: str) -> str:
        host = str(self.server.server_address[0])
        port = int(self.server.server_address[1])
        return f"http://{host}:{port}/case/{case_id}/0"


def snippet_trusting(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter_ns()
    source = case["search_record"]
    text = source["snippet_text"]
    return {
        "accepted": bool(text),
        "terminal_status": None,
        "discovered_url": source["discovered_url"],
        "discovered_at": case["discovered_at"],
        "observed_urls": [source["discovered_url"]],
        "checked_at": [],
        "retrieved_at": None,
        "published_at": source["snippet_published_at"],
        "modified_at": source["snippet_modified_at"],
        "canonical_url": None,
        "body_sha256": digest(text),
        "snippet_sha256": digest(text),
        "version_difference": False,
        "link_rot": False,
        "elapsed_us": (time.perf_counter_ns() - started) // 1000,
    }


def _canonical(headers: Any) -> str | None:
    value = headers.get("Link")
    if value and value.startswith("<") and '>; rel="canonical"' in value:
        return value[1 : value.index(">")]
    return None


def exact_fetch(case: dict[str, Any], fixture: Fixture) -> dict[str, Any]:
    started = time.perf_counter_ns()
    opener = urllib.request.build_opener(NoRedirect())
    url = fixture.url(case["case_id"])
    observed_urls: list[str] = []
    checked_at: list[str] = []
    response: Any = None
    body: bytes | None = None
    for index in range(4):
        try:
            response = opener.open(url, timeout=2)
        except urllib.error.HTTPError as exc:
            response = exc
        observed_urls.append(response.headers["X-Original-URL"])
        checked_at.append(response.headers["X-Checked-At"])
        if response.status in {301, 302, 303, 307, 308}:
            expected = case["fetch_chain"][index + 1]["request_url"]
            if response.headers["X-Original-Location"] != expected:
                raise ValueError("redirect chain differs from the frozen packet")
            url = urljoin(url, response.headers["Location"])
            continue
        body = response.read() if response.status == 200 else None
        break
    else:
        raise ValueError("redirect limit exceeded")
    text = body.decode("utf-8") if body is not None else None
    final_url = observed_urls[-1] if response.status == 200 else None
    canonical = _canonical(response.headers) or final_url
    snippet_hash = digest(case["search_record"]["snippet_text"])
    body_hash = digest(text)
    return {
        "accepted": response.status == 200 and text is not None,
        "terminal_status": response.status,
        "discovered_url": case["search_record"]["discovered_url"],
        "discovered_at": case["discovered_at"],
        "observed_urls": observed_urls,
        "checked_at": checked_at,
        "retrieved_at": checked_at[-1] if body is not None else None,
        "published_at": response.headers.get("X-Published-At"),
        "modified_at": response.headers.get("X-Modified-At"),
        "canonical_url": canonical,
        "body_sha256": body_hash,
        "snippet_sha256": snippet_hash,
        "version_difference": body_hash is not None and body_hash != snippet_hash,
        "link_rot": response.status in {404, 410},
        "elapsed_us": (time.perf_counter_ns() - started) // 1000,
    }


def score(case: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    truth = case["ground_truth"]
    expected_body = digest(truth["current_body_utf8"])
    expected_hops = [hop["request_url"] for hop in case["fetch_chain"]]
    return {
        "stale_evidence_accepted": bool(
            truth["stale_snippet"]
            and observed["accepted"]
            and observed["body_sha256"] == observed["snippet_sha256"]
        ),
        "false_rejection": bool(truth["should_accept"] and not observed["accepted"]),
        "acceptance_correct": observed["accepted"] == truth["should_accept"],
        "canonical_correct": observed["canonical_url"] == truth["final_canonical_url"],
        "body_correct": observed["body_sha256"] == expected_body,
        "link_rot_correct": observed["link_rot"] == truth["link_rot"],
        "published_at_correct": observed["published_at"] == truth["current_published_at"],
        "modified_at_correct": observed["modified_at"] == truth["current_modified_at"],
        "unknown_dates_preserved": all(
            expected is not None or actual is None
            for actual, expected in (
                (observed["published_at"], truth["current_published_at"]),
                (observed["modified_at"], truth["current_modified_at"]),
            )
        ),
        "provenance_preserved": observed["observed_urls"] == expected_hops,
        "version_difference_observed": observed["version_difference"],
        "material_conflict_exposed": bool(
            truth["conflicting_version"] and observed["version_difference"]
        ),
    }


def mark_duplicates(rows: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> None:
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        observed = row["observed"]
        if observed["accepted"] and observed["body_sha256"]:
            identity = observed["canonical_url"] or observed["discovered_url"]
            buckets[(identity, observed["body_sha256"])].append(row["case_id"])
    predicted = {case_id for ids in buckets.values() if len(ids) > 1 for case_id in ids}
    expected = {
        case_id for case_id, case in cases.items()
        if case["ground_truth"]["duplicate_group"] is not None
    }
    for row in rows:
        row["score"]["duplicate_membership_correct"] = (
            (row["case_id"] in predicted) == (row["case_id"] in expected)
        )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["score"] for row in rows]
    elapsed = [row["observed"]["elapsed_us"] for row in rows]
    keys = scores[0]
    summary = {key: sum(bool(item[key]) for item in scores) for key in keys}
    summary["material_conflict_cases"] = sum(
        row["scenario_type"] == "conflicting_versions" for row in rows
    )
    summary.update(
        cases=len(rows),
        p50_latency_us=statistics.median(elapsed),
        p95_latency_us=round(sorted(elapsed)[int((len(elapsed) - 1) * 0.95)], 3),
        provider_calls=0,
        monetary_cost=0,
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    raw = args.packet.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.packet_sha256:
        parser.error("packet digest does not match the frozen input")
    packet = json.loads(raw)
    if packet.get("schema_version") != "w8-freshness-packet/1":
        parser.error("unsupported freshness packet")
    cases = {case["case_id"]: case for case in packet["cases"]}
    arms: dict[str, list[dict[str, Any]]] = {}
    with Fixture(packet) as fixture:
        for name in ("snippet_trusting", "exact_fetch"):
            rows = []
            for case in packet["cases"]:
                observed = (
                    snippet_trusting(case) if name == "snippet_trusting"
                    else exact_fetch(case, fixture)
                )
                rows.append({
                    "arm": name,
                    "case_id": case["case_id"],
                    "scenario_type": case["scenario_type"],
                    "observed": observed,
                    "score": score(case, observed),
                })
            mark_duplicates(rows, cases)
            arms[name] = rows
    args.output.mkdir(parents=True)
    with (args.output / "results.jsonl").open("w") as stream:
        for rows in arms.values():
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
    manifest = {
        "schema_version": SCHEMA,
        "packet_sha256": args.packet_sha256,
        "scenario_counts": dict(Counter(c["scenario_type"] for c in packet["cases"])),
        "arms": {name: summarize(rows) for name, rows in arms.items()},
        "transport": "loopback-http-fixture",
        "production_adoption": False,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
