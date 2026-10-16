"""Focused contracts for the W8 freshness acquisition comparison."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_w8_freshness_acquisition.py"
SPEC = importlib.util.spec_from_file_location("w8_freshness", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def case(
    identity,
    scenario="current_page",
    *,
    status=200,
    snippet="Current body",
    body="Current body",
    canonical=None,
    published=None,
    modified=None,
    duplicate_group=None,
    conflict=False,
):
    url = f"https://source.example/{identity}"
    canonical = canonical or (url if status == 200 else None)
    hop = {
        "request_url": url,
        "status": status,
        "location": None,
        "canonical_url": canonical,
        "body_utf8": body if status == 200 else None,
        "published_at": published,
        "modified_at": modified,
        "checked_at": "2026-09-11T08:00:00Z",
    }
    return {
        "case_id": identity,
        "scenario_type": scenario,
        "discovered_at": "2026-09-11T07:00:00Z",
        "as_of": "2026-09-11T09:00:00Z",
        "search_record": {
            "discovered_url": url,
            "snippet_text": snippet,
            "snippet_published_at": None,
            "snippet_modified_at": None,
        },
        "fetch_chain": [hop],
        "ground_truth": {
            "final_canonical_url": canonical,
            "current_body_utf8": body if status == 200 else None,
            "current_published_at": published,
            "current_modified_at": modified,
            "should_accept": status == 200,
            "stale_snippet": snippet != body,
            "link_rot": status in {404, 410},
            "duplicate_group": duplicate_group,
            "conflicting_version": conflict,
        },
    }


def packet(*cases):
    return {"schema_version": "w8-freshness-packet/1", "cases": list(cases)}


def redirect_case():
    item = case("old", "redirect", body="Moved body")
    destination = "https://source.example/current"
    item["fetch_chain"] = [
        {
            **item["fetch_chain"][0],
            "status": 301,
            "location": destination,
            "canonical_url": None,
            "body_utf8": None,
        },
        {
            **item["fetch_chain"][0],
            "request_url": destination,
            "canonical_url": destination,
        },
    ]
    item["ground_truth"]["final_canonical_url"] = destination
    return item


def test_snippet_arm_does_not_claim_unobserved_acquisition_metadata():
    item = case("gone", "dead_link", status=404, snippet="Old snippet", body=None)
    observed = MODULE.snippet_trusting(item)
    assert observed["accepted"]
    assert observed["terminal_status"] is None
    assert observed["canonical_url"] is None
    assert observed["retrieved_at"] is None
    assert observed["checked_at"] == []


def test_exact_fetch_follows_redirect_and_preserves_original_provenance():
    item = redirect_case()
    data = packet(item)
    with MODULE.Fixture(data) as fixture:
        observed = MODULE.exact_fetch(item, fixture)
    assert observed["terminal_status"] == 200
    assert observed["observed_urls"] == [hop["request_url"] for hop in item["fetch_chain"]]
    assert observed["canonical_url"] == item["ground_truth"]["final_canonical_url"]
    assert observed["body_sha256"] == MODULE.digest("Moved body")


def test_exact_fetch_records_dead_link_without_accepting_snippet():
    item = case("gone", "dead_link", status=410, snippet="Old snippet", body=None)
    data = packet(item)
    with MODULE.Fixture(data) as fixture:
        observed = MODULE.exact_fetch(item, fixture)
    assert not observed["accepted"]
    assert observed["terminal_status"] == 410
    assert observed["link_rot"]
    assert observed["body_sha256"] is None
    assert observed["retrieved_at"] is None
    assert observed["checked_at"]


def test_unknown_source_dates_remain_unknown():
    item = case("undated")
    data = packet(item)
    with MODULE.Fixture(data) as fixture:
        observed = MODULE.exact_fetch(item, fixture)
    assert observed["published_at"] is None
    assert observed["modified_at"] is None
    assert MODULE.score(item, observed)["unknown_dates_preserved"]


def test_text_difference_is_observed_without_becoming_conflict_proof():
    item = case("updated", "updated_content", snippet="Earlier summary", body="New body")
    data = packet(item)
    with MODULE.Fixture(data) as fixture:
        observed = MODULE.exact_fetch(item, fixture)
    scored = MODULE.score(item, observed)
    assert observed["version_difference"]
    assert scored["version_difference_observed"]
    assert not scored["material_conflict_exposed"]


def test_canonical_and_body_duplicates_keep_both_provenance_records():
    canonical = "https://publisher.example/story"
    cases = [
        case("copy-a", "duplicated_syndication", canonical=canonical, duplicate_group="group-1"),
        case("copy-b", "duplicated_syndication", canonical=canonical, duplicate_group="group-1"),
    ]
    data = packet(*cases)
    rows = []
    with MODULE.Fixture(data) as fixture:
        for item in cases:
            observed = MODULE.exact_fetch(item, fixture)
            rows.append({
                "case_id": item["case_id"],
                "scenario_type": item["scenario_type"],
                "observed": observed,
                "score": MODULE.score(item, observed),
            })
    MODULE.mark_duplicates(rows, {item["case_id"]: item for item in cases})
    assert all(row["score"]["duplicate_membership_correct"] for row in rows)
    assert rows[0]["observed"]["discovered_url"] != rows[1]["observed"]["discovered_url"]


def test_redirect_chain_tampering_fails_closed():
    item = redirect_case()
    item["fetch_chain"][0]["location"] = "https://wrong.example/path"
    data = packet(item)
    with MODULE.Fixture(data) as fixture:
        try:
            MODULE.exact_fetch(item, fixture)
        except ValueError as exc:
            assert "redirect chain" in str(exc)
        else:
            raise AssertionError("tampered redirect chain was accepted")
