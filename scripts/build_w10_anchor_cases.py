#!/usr/bin/env python3
"""Convert the frozen W8 corpus into W10 claim-gap anchor cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_W8_SHA256 = "1dbe9ec940ffcf3cece28c9a2eb9755a6d227a651c697cc075a8e7c4b555cbbd"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def transform(payload: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for source in payload["cases"]:
        targets = source["known_useful_urls"]
        claims = [
            {
                "claim_id": f"known_useful_{index:02d}",
                "importance": 2,
                "closure_rule": target["relevance_judgment"],
            }
            for index, target in enumerate(targets, 1)
        ]
        cases.append(
            {
                "case_id": source["case_id"],
                "stratum": "w8_anchor",
                "challenge_type": source["category"],
                "as_of": source["as_of"],
                "query": source["query"],
                "claims": claims,
                "reference_targets": [
                    {
                        "claim_id": claim["claim_id"],
                        "url": target["url"],
                    }
                    for claim, target in zip(claims, targets, strict=True)
                ],
                "expected_ambiguity": source.get("expected_ambiguity"),
            }
        )
    return {
        "schema_version": "enterprise-evaluation/w10-anchor-cases/1",
        "source_corpus_sha256": EXPECTED_W8_SHA256,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    actual = digest(raw)
    if actual != EXPECTED_W8_SHA256:
        parser.error(f"expected W8 digest {EXPECTED_W8_SHA256}, found {actual}")
    result = transform(json.loads(raw))
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote {len(result['cases'])} cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
