import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import run_exact_claim_passage_jev as harness


def pair(pair_id: str, split: str, family: str) -> dict:
    passage = "Version 2 supports the claim only for public repositories."
    return {
        "pair_id": pair_id,
        "split": split,
        "claim_family_id": family,
        "source_work_id": f"work-{family}",
        "claim": "Version 2 supports the claim only for public repositories.",
        "passage": passage,
        "source": {
            "url": "https://example.org/docs",
            "title": "Documentation",
            "version": "2",
            "lineage_id": f"lineage-{family}",
        },
        "hard_negative": False,
    }


def captured_pair(tmp_path: Path, pair_id: str, split: str, family: str) -> dict:
    item = pair(pair_id, split, family)
    capture = tmp_path / f"{pair_id}.md"
    capture.write_text(item["passage"], encoding="utf-8")
    item["source"].update(
        {
            "start": 0,
            "end": len(item["passage"]),
            "capture_path": str(capture),
            "capture_sha256": harness.hashlib.sha256(
                item["passage"].encode("utf-8")
            ).hexdigest(),
        }
    )
    return item


def test_corpus_rejects_claim_family_leakage_between_splits():
    corpus = {
        "schema_version": "exact-claim-passage-corpus/1",
        "pairs": [
            pair("case-one", "calibration", "same-claim"),
            pair("case-two", "validation", "same-claim"),
        ],
    }
    with pytest.raises(ValueError, match="leaks across"):
        harness.validate_corpus(corpus)


def test_label_contract_covers_all_pairs_and_valid_categories():
    pairs = [pair("case-one", "calibration", "cal-claim")]
    harness.validate_labels(
        pairs,
        {
            "schema_version": "exact-claim-passage-labels/1",
            "label_provenance": "assistant_proposed",
            "labels": [
                {
                    "pair_id": "case-one",
                    "label": "related_insufficient",
                    "rationale": "The passage omits a required qualifier.",
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="invalid reference label"):
        harness.validate_labels(
            pairs,
            {
                "schema_version": "exact-claim-passage-labels/1",
                "label_provenance": "assistant_proposed",
                "labels": [
                    {"pair_id": "case-one", "label": "maybe", "rationale": "unclear"}
                ],
            },
        )


def test_frozen_packet_separates_labels_from_model_input(tmp_path: Path):
    corpus_path = tmp_path / "corpus.json"
    labels_path = tmp_path / "labels.json"
    output = tmp_path / "freeze"
    corpus_path.write_text(
        json.dumps(
            {
                "schema_version": "exact-claim-passage-corpus/1",
                "pairs": [
                    captured_pair(tmp_path, "case-cal", "calibration", "cal-group"),
                    captured_pair(tmp_path, "case-val", "validation", "val-group"),
                ],
            }
        ),
        encoding="utf-8",
    )
    labels_path.write_text(
        json.dumps(
            {
                "schema_version": "exact-claim-passage-labels/1",
                "label_provenance": "assistant_proposed",
                "labels": [
                    {
                        "pair_id": "case-cal",
                        "label": "supported",
                        "rationale": "Exact entailment.",
                        "reviewer_note": "must never reach Jev",
                    },
                    {
                        "pair_id": "case-val",
                        "label": "contradicted",
                        "rationale": "Version does not match.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = harness.freeze(corpus_path, labels_path, output)
    packet = json.loads((output / "jev-packet.json").read_text())
    labels = json.loads((output / "labels.json").read_text())
    assert manifest["calibration_count"] == manifest["validation_count"] == 1
    assert "label" not in packet["pairs"][0]
    assert "reviewer_note" in labels["labels"][0]


def test_jev_runner_sends_no_labels_and_validates_returned_model(tmp_path: Path):
    pair_value = harness.validate_source_pair(
        pair("case-cal", "calibration", "cal-group")
    )
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "exact-claim-passage-jev-packet/1",
                "model": harness.MODEL,
                "questions": harness.QUESTIONS,
                "pairs": [pair_value],
            }
        ),
        encoding="utf-8",
    )
    proxy = tmp_path / "proxy.py"
    proxy.write_text(
        "import json,sys\n"
        "body=sys.stdin.read()\n"
        "assert 'reference_label' not in body and 'rationale' not in body\n"
        "print(json.dumps({'model':'jev-1.13.0','answers':{'verdict':{"
        "'choice':'supported','probabilities':{'supported':0.8,"
        "'contradicted':0.05,'related_insufficient':0.1,'unverifiable':0.05}}},"
        "'usage':{'input_tokens':12,'output_tokens':3},'_elapsed_ms':4}))\n",
        encoding="utf-8",
    )
    result = harness.run(
        packet_path, tmp_path / "receipts", [sys.executable, str(proxy)]
    )
    assert result["max_in_flight"] == 1
    assert result["completed"] == 1


def test_exact_capture_offsets_reject_a_rewritten_passage(tmp_path: Path):
    item = captured_pair(tmp_path, "case-one", "calibration", "cal-group")
    item["passage"] = "A paraphrase not in the source capture."
    with pytest.raises(ValueError, match=r"passage length|does not match"):
        harness.validate_source_pair(item)


def test_semantic_runner_normalizes_case_but_preserves_ambiguous_confidence_scale(
    tmp_path: Path,
):
    item = harness.validate_source_pair(pair("case-cal", "calibration", "cal-group"))
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "exact-claim-passage-jev-packet/1",
                "pairs": [item],
            }
        ),
        encoding="utf-8",
    )
    proxy = tmp_path / "semantic_proxy.py"
    proxy.write_text(
        "import json\n"
        "print(json.dumps({'model':'free','_configured_model_alias':'free',"
        "'_elapsed_ms':5,'usage':{'prompt_tokens':20,'completion_tokens':8},"
        "'choices':[{'finish_reason':'stop','message':{'content':json.dumps("
        "{'verdict':'Supported','confidence':0.98,'reason':'entails'})}}]}))\n",
        encoding="utf-8",
    )
    result = harness.run_semantic(
        packet_path, tmp_path / "semantic_receipts", [sys.executable, str(proxy)]
    )
    assert result["completed"] == 1
    receipt = json.loads((tmp_path / "semantic_receipts" / "case-cal.json").read_text())
    assert receipt["verdict"] == "supported"
    assert receipt["confidence"] == 0.98
    assert receipt["confidence_unit"] == "ambiguous_0_to_1_or_0_to_100"
