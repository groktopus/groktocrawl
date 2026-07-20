"""Shared data for service-level research adapter tests."""

import pytest


@pytest.fixture
def research_parity_data() -> dict:
    """Provide the deterministic successful research scenario."""
    return {
        "prompt": "What did the deterministic source establish?",
        "max_searches_per_request": 1,
        "search_result": {
            "url": "https://example.test/evidence",
            "title": "Deterministic evidence",
            "description": "A controlled discovery result.",
        },
        "result": "The source established the expected fact [1].",
    }
