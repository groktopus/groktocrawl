import sys

import pytest

from scripts import grade_w10_adjudication_openai as runner


def test_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--packet",
            str(tmp_path / "packet.json"),
            "--checkpoint-dir",
            str(tmp_path / "checkpoints"),
            "--output",
            str(tmp_path / "responses.json"),
            "--base-url",
            "https://example.invalid/v1",
            "--api-key-env",
            "MISSING_TEST_KEY",
            "--model",
            "general",
        ],
    )
    with pytest.raises(ValueError, match="missing API key"):
        runner.main()
