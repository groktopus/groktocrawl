"""Deployment contracts for scraper resource isolation."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_scraper_service_runs_with_container_init():
    compose = (ROOT / "docker-compose.yml").read_text()
    match = re.search(
        r"(?ms)^  scraper-svc:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n)",
        compose,
    )

    assert match is not None
    assert re.search(r"(?m)^    init: true$", match.group("body"))
