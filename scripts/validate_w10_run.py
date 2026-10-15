#!/usr/bin/env python3
"""Validate W10 run identity, accounting, bounds, and evidence closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

TERMINAL_STOPS = {
    "all_gaps_closed",
    "no_marginal_gain",
    "search_limit",
    "model_limit",
    "source_limit",
    "time_limit",
    "fixed_query_complete",
    "planner_claimed_complete",
    "no_followup_proposed",
    "no_admitted_proposal",
    "proposal_exhausted",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def validate_run(
    run_dir: Path,
    cases_path: Path,
    freeze_path: Path,
    *,
    expected_records: int,
) -> list[str]:
    issues: list[str] = []
    metadata_path = run_dir / "run-metadata.json"
    order_path = run_dir / "work-order.json"
    if not metadata_path.exists() or not order_path.exists():
        return ["run metadata and work order must both exist"]
    metadata = load_json(metadata_path)
    freeze = load_json(freeze_path)
    cases_payload = load_json(cases_path)
    cases = {item["case_id"]: item for item in cases_payload["cases"]}
    if re.fullmatch(r"[0-9a-f]{40}", metadata.get("source_commit", "")) is None:
        issues.append("run metadata lacks an exact source commit")
    if metadata.get("cases_sha256") != digest(cases_path):
        issues.append("run metadata case digest does not match")
    if metadata.get("freeze_sha256") != digest(freeze_path):
        issues.append("run metadata freeze digest does not match")
    if metadata.get("runner_sha256") != freeze.get("runner_sha256"):
        issues.append("run metadata runner digest does not match the freeze")
    if metadata.get("search_environment_sha256") != freeze.get(
        "search_environment_sha256"
    ):
        issues.append("run metadata search environment does not match the freeze")

    entries = load_json(order_path).get("entries", [])
    if len(entries) != expected_records:
        issues.append(
            f"work order has {len(entries)} entries; expected {expected_records}"
        )
    planned = {
        (item["case_id"], item["policy"], item["repetition"]) for item in entries
    }
    if len(planned) != len(entries):
        issues.append("work order contains duplicate trial identities")

    record_paths = sorted((run_dir / "records").glob("*.json"))
    private_paths = {
        item.name: item for item in (run_dir / "private-acquisitions").glob("*.json")
    }
    observed: set[tuple[str, str, int]] = set()
    for record_path in record_paths:
        record = load_json(record_path)
        identity = (record["case_id"], record["policy"], record["repetition"])
        if identity not in planned:
            issues.append(f"{record_path.name}: trial is absent from the work order")
        if identity in observed:
            issues.append(f"{record_path.name}: duplicate completed trial identity")
        observed.add(identity)
        if record.get("status") != "completed":
            partial = record.get("partial_evidence_file")
            if isinstance(partial, str):
                partial_path = run_dir / partial
                if not partial_path.exists():
                    issues.append(
                        f"{record_path.name}: partial evidence file is missing"
                    )
                else:
                    checkpoint = load_json(partial_path)
                    if str(checkpoint.get("stage", "")).endswith(
                        "_assessment_received"
                    ):
                        if not isinstance(
                            checkpoint.get("received_assessment"), dict
                        ):
                            issues.append(
                                f"{record_path.name}: failed assessment is not preserved"
                            )
                        if not isinstance(
                            checkpoint.get("received_assessment_receipt"), dict
                        ):
                            issues.append(
                                f"{record_path.name}: failed assessment receipt is not preserved"
                            )
            continue
        if record.get("stop_reason") not in TERMINAL_STOPS:
            issues.append(f"{record_path.name}: missing terminal stop reason")
        metrics = record.get("metrics", {})
        for key, maximum in {
            "searches": 3,
            "model_calls": 3,
            "admitted_count": 8,
            "elapsed_ms": 180_000,
        }.items():
            if metrics.get(key, maximum + 1) > maximum:
                issues.append(f"{record_path.name}: {key} exceeds {maximum}")
        attempts = record.get("attempts", [])
        proposals = record.get("proposals", [])
        if metrics.get("searches") != len(attempts):
            issues.append(
                f"{record_path.name}: search accounting differs from attempts"
            )
        if sum(item.get("executed", False) for item in proposals) != max(
            0, len(attempts) - 1
        ):
            issues.append(f"{record_path.name}: proposal execution accounting differs")
        if any("reviewed_excerpt" in item for item in record.get("candidates", [])):
            issues.append(
                f"{record_path.name}: public record contains a private excerpt"
            )
        if record.get("orchestration", {}).get("runtime") != "langgraph":
            issues.append(f"{record_path.name}: orchestration runtime is not LangGraph")
        event_count = record.get("orchestration", {}).get("event_count")
        event_digests = record.get("orchestration", {}).get("event_digests", [])
        if event_count != len(event_digests):
            issues.append(f"{record_path.name}: event trace count differs")

        private_path = private_paths.get(record_path.name)
        if private_path is None:
            issues.append(f"{record_path.name}: private acquisition record is missing")
            continue
        private = {item["candidate_id"]: item for item in load_json(private_path)}
        public_candidates = record.get("candidates", [])
        public_ids = [item.get("candidate_id") for item in public_candidates]
        if len(public_ids) != len(set(public_ids)):
            issues.append(f"{record_path.name}: candidate IDs are not unique")
        if set(public_ids) != set(private):
            issues.append(f"{record_path.name}: public/private candidate sets differ")
        for item in public_candidates:
            candidate_id = item.get("candidate_id")
            private_item = private.get(candidate_id, {})
            if item.get("reviewed_bytes_sha256") != private_item.get(
                "reviewed_bytes_sha256"
            ):
                issues.append(
                    f"{record_path.name}: candidate {candidate_id} digest differs"
                )
            if item.get("acquisition_status") != "not_selected":
                if "selected_on_attempt" not in item:
                    issues.append(
                        f"{record_path.name}: candidate {candidate_id} lacks acquisition attribution"
                    )
                selected_on = item.get("selected_on_attempt")
                origin_attempts = {
                    origin["attempt"] for origin in item.get("search_origins", [])
                }
                if selected_on is not None and selected_on not in origin_attempts:
                    issues.append(
                        f"{record_path.name}: candidate {candidate_id} attribution lacks a matching origin"
                    )
        expected_gaps = {
            item["claim_id"] for item in cases[record["case_id"]]["claims"]
        }
        initial_gap_assessment = record.get("initial_gap_assessment")
        if record["policy"] == "fixed":
            if initial_gap_assessment is not None:
                issues.append(
                    f"{record_path.name}: fixed policy has a planner gap assessment"
                )
        elif not isinstance(initial_gap_assessment, list):
            issues.append(
                f"{record_path.name}: adaptive policy lacks initial gap assessment"
            )
        else:
            initial_gap_ids = [
                item.get("gap_id")
                for item in initial_gap_assessment
                if isinstance(item, dict)
            ]
            if (
                len(initial_gap_ids) != len(initial_gap_assessment)
                or len(initial_gap_ids) != len(set(initial_gap_ids))
                or set(initial_gap_ids) != expected_gaps
            ):
                issues.append(
                    f"{record_path.name}: initial gap assessment differs from the case"
                )
            else:
                initial_gap_statuses = {
                    item["gap_id"]: item.get("status")
                    for item in initial_gap_assessment
                }
                for proposal in proposals:
                    if (
                        proposal.get("admitted")
                        and initial_gap_statuses.get(proposal.get("gap_id")) == "closed"
                    ):
                        issues.append(
                            f"{record_path.name}: admitted proposal targets a planner-closed gap"
                        )
        round_decisions = record.get("round_decisions", [])
        interim_assessment = record.get("interim_assessment")
        if round_decisions:
            if not isinstance(interim_assessment, dict):
                issues.append(
                    f"{record_path.name}: round decision lacks preserved interim assessment"
                )
            else:
                interim_gaps = interim_assessment.get("gaps", [])
                interim_gap_ids = [
                    item.get("gap_id")
                    for item in interim_gaps
                    if isinstance(item, dict)
                ]
                if (
                    len(interim_gap_ids) != len(interim_gaps)
                    or len(interim_gap_ids) != len(set(interim_gap_ids))
                    or set(interim_gap_ids) != expected_gaps
                ):
                    issues.append(
                        f"{record_path.name}: interim gap assessment differs from the case"
                    )
                elif round_decisions[-1].get("all_gaps_closed") != all(
                    item.get("status") == "closed" for item in interim_gaps
                ):
                    issues.append(
                        f"{record_path.name}: round stop disagrees with preserved interim gaps"
                    )
        elif interim_assessment is not None:
            issues.append(
                f"{record_path.name}: interim assessment exists without a round decision"
            )
        observed_gaps = [item["gap_id"] for item in record.get("gap_results", [])]
        if (
            len(observed_gaps) != len(set(observed_gaps))
            or set(observed_gaps) != expected_gaps
        ):
            issues.append(f"{record_path.name}: gap accounting differs from the case")

    orphan_private = set(private_paths) - {item.name for item in record_paths}
    if orphan_private:
        issues.append(
            f"{len(orphan_private)} private completed records lack public records"
        )
    for public_name, private_name in (
        ("inflight", "private-inflight"),
        ("failures", "private-failures"),
    ):
        public_evidence = {item.name for item in (run_dir / public_name).glob("*.json")}
        private_evidence = {
            item.name for item in (run_dir / private_name).glob("*.json")
        }
        if public_evidence != private_evidence:
            issues.append(f"{public_name} and {private_name} evidence sets differ")

    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        if manifest.get("records") != len(record_paths):
            issues.append("manifest record count differs from retained records")
        completed = sum(
            load_json(path).get("status") == "completed" for path in record_paths
        )
        if manifest.get("completed") != completed:
            issues.append("manifest completed count differs from retained records")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, required=True)
    args = parser.parse_args()
    issues = validate_run(
        args.run_dir,
        args.cases,
        args.freeze,
        expected_records=args.expected_records,
    )
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
