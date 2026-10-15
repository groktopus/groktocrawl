#!/usr/bin/env python3
"""Validate closure, replay, and blinding for every W12.1 experiment lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.mission_experiment import (
    GradeWorkItem,
    IntakeGradeWorkItem,
    IntakeWorkItem,
    WorkItem,
    build_adjudication_prompt,
    build_downstream_prompt,
    build_grade_prompt,
    build_intake_grade_prompt,
    build_intake_prompt,
    sealed_candidate_id,
    sealed_grade_candidate,
    sealed_intake_candidate_id,
    validate_candidate_grade,
    validate_downstream_result,
    validate_intake_grade,
    validate_intake_result,
)
from agent.experimental.research_mission import load_mission_experiment_corpus


def load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def source_index(path: Path) -> dict[str, dict[str, str]]:
    return {
        item["source_id"]: {
            "source_id": item["source_id"],
            "title": item["title"],
            "text": item["text"],
        }
        for item in load(path)["sources"]
    }


def check_private_completion(
    private_path: Path,
    public: dict[str, Any],
    *,
    identity: str,
    expected_prompt: dict[str, Any],
    issues: list[str],
) -> str | None:
    if not private_path.exists():
        issues.append(f"{identity}: private record is missing")
        return None
    if stat.S_IMODE(private_path.stat().st_mode) != 0o600:
        issues.append(f"{identity}: private record mode is not 0600")
    private = load(private_path)
    content = private.get("raw_completion")
    envelope = private.get("raw_envelope")
    receipt = private.get("receipt") or {}
    if not isinstance(content, str):
        issues.append(f"{identity}: raw completion is missing")
        return None
    if not isinstance(envelope, str):
        issues.append(f"{identity}: raw envelope is missing")
    elif (
        receipt.get("envelope_sha256") != hashlib.sha256(envelope.encode()).hexdigest()
    ):
        issues.append(f"{identity}: envelope digest does not close")
    if receipt.get("response_sha256") != hashlib.sha256(content.encode()).hexdigest():
        issues.append(f"{identity}: response digest does not close")
    if public.get("receipt") != receipt:
        issues.append(f"{identity}: public and private receipts differ")
    if private.get("prompt") != expected_prompt:
        issues.append(f"{identity}: retained prompt differs from frozen inputs")
    return content


def check_manifest(
    run_dir: Path,
    *,
    manifest_name: str,
    summary_name: str,
    work_name: str,
    expected_revision: str,
    expected_count: int | None,
    issues: list[str],
    allow_failures: bool = True,
) -> dict[str, Any] | None:
    manifest_path = run_dir / "public" / manifest_name
    summary_path = run_dir / "public" / summary_name
    if not manifest_path.exists() or not summary_path.exists():
        issues.append(f"{manifest_name}: manifest or summary is missing")
        return None
    manifest, summary = load(manifest_path), load(summary_path)
    work = load(ROOT / "docs/experiments/research-mission" / work_name)
    if manifest.get("source_revision") != expected_revision:
        issues.append(f"{manifest_name}: source revision differs")
    if manifest.get("work_order_sha256") != work["trials_sha256"]:
        issues.append(f"{manifest_name}: work order differs")
    count = expected_count
    if count is None:
        count = manifest.get("eligible_candidate_count")
    if not isinstance(count, int) or summary.get("attempted") != count:
        issues.append(f"{summary_name}: attempted count differs")
    completed, failed = summary.get("completed"), summary.get("failed")
    if not isinstance(count, int) or completed + failed != count:
        issues.append(f"{summary_name}: terminal count differs")
    elif (not allow_failures and failed) or (count and failed / count > 0.10):
        issues.append(f"{summary_name}: failure guardrail exceeded")
    return work


def validate_run(
    run_dir: Path,
    *,
    expected_revision: str,
    inherited_primary_revision: str | None = None,
) -> list[str]:
    issues: list[str] = []
    experiment_dir = ROOT / "docs/experiments/research-mission"
    source_path = ROOT / "docs/experiments/enterprise-evaluation/corpus.json"
    corpus = load_mission_experiment_corpus(
        experiment_dir / "w12.1-cases.json", source_corpus_path=source_path
    )
    cases = {item.case_id: item for item in corpus.cases}
    sources = source_index(source_path)
    primary_revision = inherited_primary_revision or expected_revision
    candidates = _validate_downstream(
        run_dir, primary_revision, cases, sources, issues
    )
    intake_candidates = _validate_intake(run_dir, primary_revision, cases, issues)
    _validate_downstream_grades(
        run_dir, primary_revision, cases, sources, candidates, issues
    )
    _validate_intake_grades(
        run_dir, primary_revision, cases, intake_candidates, issues
    )
    _validate_adjudications(
        run_dir, expected_revision, cases, sources, candidates, issues
    )
    return issues


def _validate_adjudications(
    run_dir: Path,
    expected_revision: str,
    cases: dict[str, Any],
    sources: dict[str, dict[str, str]],
    candidates: dict[str, dict[str, Any]],
    issues: list[str],
) -> None:
    public_dir = run_dir / "public"
    work_path = public_dir / "adjudication-work-order.json"
    manifest_path = public_dir / "adjudication-manifest.json"
    summary_path = public_dir / "adjudication-summary.json"
    if not all(path.exists() for path in (work_path, manifest_path, summary_path)):
        issues.append("adjudication: work order, manifest, or summary is missing")
        return
    work, manifest, summary = load(work_path), load(manifest_path), load(summary_path)
    rows = work.get("trials", [])
    expected_digest = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    if work.get("trials_sha256") != expected_digest:
        issues.append("adjudication: work order digest does not close")
    selection_digest = hashlib.sha256(
        json.dumps(work, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    if manifest.get("selection_sha256") != selection_digest:
        issues.append("adjudication: selection digest does not close")
    if manifest.get("work_order_sha256") != expected_digest:
        issues.append("adjudication: manifest work order differs")
    if manifest.get("source_revision") != expected_revision:
        issues.append("adjudication: source revision differs")
    if summary.get("attempted") != len(rows):
        issues.append("adjudication: attempted count differs")
    completed, failed = summary.get("completed"), summary.get("failed")
    if completed + failed != len(rows):
        issues.append("adjudication: terminal count differs")
    elif rows and failed / len(rows) > 0.10:
        issues.append("adjudication: failure guardrail exceeded")
    if len({row.get("candidate_id") for row in rows}) != len(rows):
        issues.append("adjudication: candidate identity is duplicated")
    for row in rows:
        candidate_id = row.get("candidate_id")
        case_id = row.get("case_id")
        if candidate_id not in candidates or case_id not in cases:
            issues.append(f"{candidate_id}: adjudication candidate or case is missing")
            continue
        public_path = public_dir / "adjudications" / f"{candidate_id}.json"
        private_path = run_dir / "private/adjudications" / f"{candidate_id}.json"
        if not public_path.exists():
            issues.append(f"{candidate_id}: adjudication is missing")
            continue
        public = load(public_path)
        if public.get("status") != "completed":
            if public.get("status") != "failed":
                issues.append(f"{candidate_id}: adjudication is not terminal")
            continue
        case = cases[case_id]
        source_pack = tuple(sources[source_id] for source_id in case.source_ids)
        content = check_private_completion(
            private_path,
            public,
            identity=f"{candidate_id} adjudication",
            expected_prompt=build_adjudication_prompt(
                case, sources=source_pack, candidate=candidates[candidate_id]
            ),
            issues=issues,
        )
        if content is None:
            continue
        try:
            replayed = validate_candidate_grade(
                json.loads(content), case=case
            ).model_dump(mode="json")
        except Exception as error:
            issues.append(f"{candidate_id}: adjudication is invalid: {error}")
            continue
        if public.get("grade") != replayed:
            issues.append(f"{candidate_id}: adjudication does not replay")


def _validate_downstream(
    run_dir: Path,
    expected_revision: str,
    cases: dict[str, Any],
    sources: dict[str, dict[str, str]],
    issues: list[str],
) -> dict[str, dict[str, Any]]:
    work = check_manifest(
        run_dir,
        manifest_name="run-manifest.json",
        summary_name="run-summary.json",
        work_name="w12.1-work-order.json",
        expected_revision=expected_revision,
        expected_count=72,
        issues=issues,
    )
    candidates: dict[str, dict[str, Any]] = {}
    if work is None:
        return candidates
    for row in work["trials"]:
        item = WorkItem(**row)
        public_path = run_dir / "public/trials" / f"{item.trial_id}.json"
        private_path = run_dir / "private/trials" / f"{item.trial_id}.json"
        if not public_path.exists():
            issues.append(f"{item.trial_id}: downstream record is missing")
            continue
        public = load(public_path)
        expected = {
            "trial_id": item.trial_id,
            "case_id": item.case_id,
            "arm": item.arm,
            "repetition": item.repetition,
            "position": item.position,
        }
        if any(public.get(key) != value for key, value in expected.items()):
            issues.append(f"{item.trial_id}: identity differs from work order")
        if public.get("status") != "completed":
            if public.get("status") != "failed":
                issues.append(f"{item.trial_id}: downstream trial is not terminal")
            continue
        case = cases[item.case_id]
        source_pack = tuple(sources[source_id] for source_id in case.source_ids)
        content = check_private_completion(
            private_path,
            public,
            identity=item.trial_id,
            expected_prompt=build_downstream_prompt(
                case, sources=source_pack, arm=item.arm
            ),
            issues=issues,
        )
        if content is None:
            continue
        try:
            validated = validate_downstream_result(
                json.loads(content), case=case, arm=item.arm
            )
        except Exception as error:
            issues.append(f"{item.trial_id}: downstream completion is invalid: {error}")
            continue
        candidate_id = sealed_candidate_id(item.trial_id)
        candidate = sealed_grade_candidate(validated, candidate_id=candidate_id)
        if public.get("sealed_candidate") != candidate:
            issues.append(f"{item.trial_id}: sealed candidate does not replay")
        if candidate_id in candidates:
            issues.append(f"{item.trial_id}: candidate identity is duplicated")
        candidates[candidate_id] = candidate
    return candidates


def _validate_intake(
    run_dir: Path,
    expected_revision: str,
    cases: dict[str, Any],
    issues: list[str],
) -> dict[str, dict[str, Any]]:
    work = check_manifest(
        run_dir,
        manifest_name="intake-manifest.json",
        summary_name="intake-summary.json",
        work_name="w12.1-intake-work-order.json",
        expected_revision=expected_revision,
        expected_count=36,
        issues=issues,
    )
    candidates: dict[str, dict[str, Any]] = {}
    if work is None:
        return candidates
    for row in work["trials"]:
        item = IntakeWorkItem(**row)
        public_path = run_dir / "public/intake" / f"{item.trial_id}.json"
        private_path = run_dir / "private/intake" / f"{item.trial_id}.json"
        if not public_path.exists():
            issues.append(f"{item.trial_id}: intake record is missing")
            continue
        public = load(public_path)
        if public.get("status") != "completed":
            if public.get("status") != "failed":
                issues.append(f"{item.trial_id}: intake is not terminal")
            continue
        case = cases[item.case_id]
        content = check_private_completion(
            private_path,
            public,
            identity=item.trial_id,
            expected_prompt=build_intake_prompt(case),
            issues=issues,
        )
        if content is None:
            continue
        try:
            replayed = validate_intake_result(json.loads(content)).model_dump(
                mode="json"
            )
        except Exception as error:
            issues.append(f"{item.trial_id}: intake completion is invalid: {error}")
            continue
        if public.get("result") != replayed:
            issues.append(f"{item.trial_id}: intake result does not replay")
        candidate_id = sealed_intake_candidate_id(item.trial_id)
        candidates[candidate_id] = {
            "candidate_id": candidate_id,
            "result": replayed,
        }
    return candidates


def _validate_downstream_grades(
    run_dir: Path,
    expected_revision: str,
    cases: dict[str, Any],
    sources: dict[str, dict[str, str]],
    candidates: dict[str, dict[str, Any]],
    issues: list[str],
) -> None:
    work = check_manifest(
        run_dir,
        manifest_name="grade-manifest.json",
        summary_name="grade-summary.json",
        work_name="w12.1-grade-work-order.json",
        expected_revision=expected_revision,
        expected_count=None,
        issues=issues,
    )
    if work is None:
        return
    frozen_ids = {row["candidate_id"] for row in work["trials"]}
    if not set(candidates) <= frozen_ids:
        issues.append("grade candidates fall outside frozen work order")
    for row in (row for row in work["trials"] if row["candidate_id"] in candidates):
        item = GradeWorkItem(**row)
        public_path = run_dir / "public/grades" / f"{item.candidate_id}.json"
        private_path = run_dir / "private/grades" / f"{item.candidate_id}.json"
        if not public_path.exists() or item.candidate_id not in candidates:
            issues.append(f"{item.candidate_id}: grade or candidate is missing")
            continue
        public = load(public_path)
        if public.get("status") != "completed":
            if public.get("status") != "failed":
                issues.append(f"{item.candidate_id}: grade is not terminal")
            continue
        case = cases[item.case_id]
        source_pack = tuple(sources[source_id] for source_id in case.source_ids)
        content = check_private_completion(
            private_path,
            public,
            identity=item.candidate_id,
            expected_prompt=build_grade_prompt(
                case,
                sources=source_pack,
                candidate=candidates[item.candidate_id],
            ),
            issues=issues,
        )
        if content is None:
            continue
        try:
            replayed = validate_candidate_grade(
                json.loads(content), case=case
            ).model_dump(mode="json")
        except Exception as error:
            issues.append(f"{item.candidate_id}: grade is invalid: {error}")
            continue
        if public.get("grade") != replayed:
            issues.append(f"{item.candidate_id}: grade does not replay")


def _validate_intake_grades(
    run_dir: Path,
    expected_revision: str,
    cases: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    issues: list[str],
) -> None:
    work = check_manifest(
        run_dir,
        manifest_name="intake-grade-manifest.json",
        summary_name="intake-grade-summary.json",
        work_name="w12.1-intake-grade-work-order.json",
        expected_revision=expected_revision,
        expected_count=None,
        issues=issues,
    )
    if work is None:
        return
    frozen_ids = {row["candidate_id"] for row in work["trials"]}
    if not set(candidates) <= frozen_ids:
        issues.append("intake grade candidates fall outside frozen work order")
    for row in (row for row in work["trials"] if row["candidate_id"] in candidates):
        item = IntakeGradeWorkItem(**row)
        public_path = run_dir / "public/intake-grades" / f"{item.candidate_id}.json"
        private_path = run_dir / "private/intake-grades" / f"{item.candidate_id}.json"
        if not public_path.exists() or item.candidate_id not in candidates:
            issues.append(f"{item.candidate_id}: intake grade or candidate is missing")
            continue
        public = load(public_path)
        if public.get("status") != "completed":
            if public.get("status") != "failed":
                issues.append(f"{item.candidate_id}: intake grade is not terminal")
            continue
        case = cases[item.case_id]
        content = check_private_completion(
            private_path,
            public,
            identity=item.candidate_id,
            expected_prompt=build_intake_grade_prompt(
                case, candidate=candidates[item.candidate_id]
            ),
            issues=issues,
        )
        if content is None:
            continue
        try:
            replayed = validate_intake_grade(json.loads(content)).model_dump(
                mode="json"
            )
        except Exception as error:
            issues.append(f"{item.candidate_id}: intake grade is invalid: {error}")
            continue
        if public.get("grade") != replayed:
            issues.append(f"{item.candidate_id}: intake grade does not replay")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--inherited-primary-revision")
    args = parser.parse_args()
    issues = validate_run(
        args.run_dir,
        expected_revision=args.expected_revision,
        inherited_primary_revision=args.inherited_primary_revision,
    )
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
