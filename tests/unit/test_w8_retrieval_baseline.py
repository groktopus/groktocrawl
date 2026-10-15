import importlib.util
from pathlib import Path

_PATH = Path(__file__).parents[2] / "scripts" / "run_w8_retrieval_baseline.py"
_SPEC = importlib.util.spec_from_file_location("w8_retrieval_baseline", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_canonical_url_is_conservative_and_github_case_insensitive():
    assert (
        _MODULE.canonical_url("https://www.GitHub.com/SWE-agent/SWE-agent/#readme")
        == "https://github.com/swe-agent/swe-agent"
    )
    assert _MODULE.canonical_url("https://example.com/Case/?a=1#x") == (
        "https://example.com/Case?a=1"
    )


def test_score_results_counts_recall_rank_and_duplicates():
    case = {
        "known_useful_urls": [
            {"url": "https://github.com/Org/Repo", "relevance_judgment": "primary"},
            {"url": "https://example.com/other", "relevance_judgment": "secondary"},
        ]
    }
    results = [
        {"url": "https://irrelevant.test/"},
        {"url": "https://github.com/org/repo/"},
        {"url": "https://github.com/org/repo"},
    ]
    score = _MODULE.score_results(case, results)
    assert score == {
        "known_useful_total": 2,
        "known_useful_found": 1,
        "known_useful_recall": 0.5,
        "first_useful_rank": 2,
        "matched_known_urls": ["https://github.com/Org/Repo"],
        "duplicate_results": 1,
        "duplicate_rate": 0.333333,
    }
