#!/usr/bin/env python3
"""Render the completed W10 evidence as a human-readable Artifact Pyramid."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def load(path: Path, schema: str, *, complete: bool = False) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise ValueError(f"{path.name} has an unsupported schema")
    if complete and value.get("complete") is not True:
        raise ValueError(f"{path.name} is incomplete")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percent(value: Any) -> str:
    return "unavailable" if not isinstance(value, (int, float)) else f"{value:.1%}"


def number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def decision_text(decision: str, selected: list[str]) -> str:
    if decision == "retain_fixed_default":
        return (
            "Keep fixed-query retrieval as the default. The tested adaptive policy did "
            "not clear every frozen replacement gate."
        )
    if decision == "allow_bounded_adaptation_for_selected_types":
        names = ", ".join(f"`{item}`" for item in selected) or "the selected cases"
        return (
            "Keep fixed-query retrieval as the default and allow bounded adaptation only "
            f"for these demonstrated gap types: {names}."
        )
    raise ValueError(f"unsupported W10 decision: {decision}")


def sources(*items: str) -> str:
    return "\n## SOURCES\n\n" + "\n".join(f"- {item}" for item in items) + "\n"


def _policy_rows(summary: dict[str, Any]) -> str:
    policies = summary.get("challenge", {}).get("summaries", {})
    rows = ["| Policy | Closure | Precision | Trials |", "|---|---:|---:|---:|"]
    for name, values in sorted(policies.items()):
        rows.append(
            f"| `{name}` | {percent(values.get('weighted_closure'))} | "
            f"{percent(values.get('precision'))} | {number(values.get('trials'))} |"
        )
    return "\n".join(rows)


def _variation_rows(accounting: dict[str, Any]) -> str:
    rows = [
        "| Policy | Trials | Searches median | Admitted median | Elapsed median |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, values in sorted(accounting["variation"]["by_policy"].items()):
        rows.append(
            f"| `{name}` | {values['trials']} | {number(values['searches']['median'])} | "
            f"{number(values['admitted']['median'])} | "
            f"{number(values['elapsed_ms']['median'])} ms |"
        )
    return "\n".join(rows)


def render(
    summary: dict[str, Any],
    accounting: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    input_digests: dict[str, str],
) -> dict[str, str]:
    if adjudication.get("analysis_role") != (
        "declared sensitivity; frozen model-graded primary is unchanged"
    ):
        raise ValueError("adjudication is not labeled as a sensitivity")
    if adjudication.get("primary_summary_sha256") != input_digests["summary"]:
        raise ValueError("adjudication does not bind the supplied primary summary")
    if accounting.get("inputs", {}).get("summary_sha256") != input_digests["summary"]:
        raise ValueError("accounting does not bind the supplied primary summary")

    decision = str(summary["decision"])
    selected = [str(item) for item in summary.get("selected_challenge_types", [])]
    verdict = decision_text(decision, selected)
    changed = adjudication.get("decision_changed") is True
    sensitivity = str(adjudication.get("adjudicated_sensitivity_decision"))
    anchor = summary["anchor"]
    totals = accounting["totals"]

    index = """# W10 adaptive research policy evidence

This package explains whether GroktoCrawl should replace fixed-query retrieval
with the tested bounded adaptive policy. Start with the findings, use the
analysis for the comparisons, and consult the dossiers for audit detail.

1. [Findings](01-summary/findings.md)
2. [Policy effects](02-analysis/policy-effects.md)
3. [Boundary and sensitivity analysis](02-analysis/boundaries-and-sensitivities.md)
4. [Study accounting](03-dossiers/accounting.md)
5. [Independent adjudication](03-dossiers/adjudication.md)
6. [Method and limits](03-dossiers/method.md)

The frozen model-graded analysis is primary. Independent agent adjudication is
reported as a sensitivity and does not overwrite it.
""" + sources(
        "[Frozen protocol](../w10-frozen-protocol.md)",
        "[Analysis plan](../w10-analysis-plan.md)",
    )

    findings = f"""# Findings

## Decision

{verdict}

## What the experiment established

- The complete record contains {totals["completed_trials"]} completed trials and
  {totals["failed_trials"]} failed trials.
- The W8 anchor gate {"passed" if anchor["gate"]["passed"] else "did not pass"}.
- The primary decision selected {len(selected)} adaptive challenge type(s).
- Independent adjudication {"changed" if changed else "did not change"} the
  decision in the declared sensitivity analysis. Its result was `{sensitivity}`.

## Practical consequence

Any adaptive behavior should use the tested trigger, proposal, admission, stop,
and budget rules. Results do not justify unbounded autonomous search or claims
outside the frozen domain and cases.
""" + sources(
        "[Policy effects](../02-analysis/policy-effects.md)",
        "[Boundary and sensitivity analysis](../02-analysis/boundaries-and-sensitivities.md)",
        "[Study accounting](../03-dossiers/accounting.md)",
    )

    policy = f"""# Policy effects

The challenge comparison separates query generation, gap binding, proposal
gating, marginal-value admission, and deterministic stopping. The table reports
the frozen model grades; it is descriptive across the fixed cases and three
repetitions.

{_policy_rows(summary)}

## Anchor gate

- Weighted-closure change, full versus fixed: {percent(anchor["gate"]["closure_delta"])}
- Precision change, full versus fixed: {percent(anchor["gate"]["precision_delta"])}
- Frozen non-inferiority gate: **{"pass" if anchor["gate"]["passed"] else "fail"}**

Passing the anchor means the policy did not exceed the declared degradation
margin on the reused W8 cases. It does not itself prove a benefit.
""" + sources(
        "[Machine-readable primary summary](../03-dossiers/method.md)",
        "[Frozen protocol](../../w10-frozen-protocol.md)",
    )

    loo = summary.get("sensitivity", {}).get("leave_one_challenge_case_out", {})
    changed_cases = [
        case_id for case_id, value in loo.items() if sorted(value) != sorted(selected)
    ]
    boundary = f"""# Boundary and sensitivity analysis

## Where the conclusion is fragile

- Removing one challenge case changed the selected gap types in
  {len(changed_cases)} of {len(loo)} leave-one-case-out checks.
- Equal claim weights selected:
  {", ".join(summary["sensitivity"]["equal_claim_weights"]["selected_types"]) or "none"}.
- Unavailable sources were excluded from admission and claim closure in the
  primary calculation.
- Ambiguous claim closure was treated as open.

## Independent review

The independent agent review was blind to policy and repetition. Its sensitivity
decision was `{sensitivity}`; this {"differs from" if changed else "matches"} the
primary decision `{decision}`. Agreement details and selection reasons are in the
adjudication dossier.

These checks describe dependence on grading and case composition. They do not
turn this fixed-case experiment into a population estimate.
""" + sources(
        "[Adjudication dossier](../03-dossiers/adjudication.md)",
        "[Analysis plan](../../w10-analysis-plan.md)",
    )

    accounting_doc = f"""# Study accounting

All completion gates passed. The public accounting record contains no query text,
URLs, titles, excerpts, model responses, private paths, or credentials.

| Item | Count |
|---|---:|
| Expected trials | {totals["expected_trials"]} |
| Observed trials | {totals["observed_trials"]} |
| Executed queries | {totals["executed_queries"]} |
| Candidate records | {totals["candidate_records"]} |
| Search-result sightings | {totals["search_result_sightings"]} |
| Acquired candidates | {totals["acquired_candidates"]} |
| Admitted candidates | {totals["admitted_candidates"]} |
| Excluded candidates | {totals["excluded_candidates"]} |
| Source-to-claim links | {totals["source_to_claim_links"]} |

## Variation by policy

{_variation_rows(accounting)}

The private acquisition manifest binds {accounting["inputs"]["private_acquisition_manifest"]["files"]}
files by digest without publishing their contents.
""" + sources(
        "`w10-accounting.json` in the retained evidence package",
        "[Method and limits](method.md)",
    )

    agreement = adjudication["agreement"]
    adjudication_doc = f"""# Independent adjudication

The reviewer was an agent operating on a packet blinded to policy and repetition.
The review covered the seeded source sample and all frozen disagreement classes.
It is a sensitivity analysis; the primary model grades remain unchanged.

| Judgment | Reviewed | Agreement |
|---|---:|---:|
| Source usefulness | {agreement["source_usefulness"]["reviewed"]} | {percent(agreement["source_usefulness"]["agreement"])} |
| Source quality components | {agreement["source_quality_components"]["reviewed"]} | {percent(agreement["source_quality_components"]["agreement"])} |
| Claim status | {agreement["claim_status"]["reviewed"]} | {percent(agreement["claim_status"]["agreement"])} |

- Primary decision: `{decision}`
- Adjudicated sensitivity: `{sensitivity}`
- Decision changed: **{"yes" if changed else "no"}**

Agreement measures consistency, not truth. Any material disagreement remains part
of the decision record.
""" + sources(
        "`w10-adjudication-analysis.json` in the retained evidence package",
        "[Boundary and sensitivity analysis](../02-analysis/boundaries-and-sensitivities.md)",
    )

    method = f"""# Method and limits

The study used 12 challenge cases, 24 W8 anchor cases, five policies, and three
repetitions. It preserved every trial, query, candidate disposition, acquired
source digest, source-to-claim link, proposal decision, stop reason, failure, and
adjudication selected by the frozen protocol.

## Input identities

- Primary summary SHA-256: `{input_digests["summary"]}`
- Public accounting SHA-256: `{input_digests["accounting"]}`
- Adjudication analysis SHA-256: `{input_digests["adjudication"]}`

## Limits

Repeated runs reuse the same questions, so they measure run variability rather
than independent population samples. Live search results are time-sensitive.
Model judgments were independently sampled and checked, but this remains an
agent-adjudicated experiment rather than human-labeled ground truth. The result
applies to the frozen models, prompts, cases, search environment, and bounds.
""" + sources(
        "[Research brief](../../w10-research-brief.md)",
        "[Frozen protocol](../../w10-frozen-protocol.md)",
        "[Analysis plan](../../w10-analysis-plan.md)",
        "[Research log](../../w10-research-log.md)",
    )

    return {
        "00-index.md": index,
        "01-summary/findings.md": findings,
        "02-analysis/policy-effects.md": policy,
        "02-analysis/boundaries-and-sensitivities.md": boundary,
        "03-dossiers/accounting.md": accounting_doc,
        "03-dossiers/adjudication.md": adjudication_doc,
        "03-dossiers/method.md": method,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--accounting", type=Path, required=True)
    parser.add_argument("--adjudication-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = load(args.summary, "enterprise-evaluation/w10-summary/1", complete=True)
    accounting = load(
        args.accounting, "enterprise-evaluation/w10-public-accounting/1", complete=True
    )
    adjudication = load(
        args.adjudication_analysis,
        "enterprise-evaluation/w10-adjudication-analysis/1",
    )
    documents = render(
        summary,
        accounting,
        adjudication,
        input_digests={
            "summary": digest(args.summary),
            "accounting": digest(args.accounting),
            "adjudication": digest(args.adjudication_analysis),
        },
    )
    args.output.mkdir(parents=True, exist_ok=False)
    for relative, content in documents.items():
        path = args.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print(f"rendered {len(documents)} W10 Artifact Pyramid documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
