"""CLI preview contract, including explicit override and dry-run behavior."""

import argparse
import json
from unittest.mock import MagicMock

from tests.service.test_cli import _cli_ns


def test_cli_passes_request_unchanged_and_prints_proposal(capsys):
    client = MagicMock(dry_run=False)
    client.followup_preview.return_value = {
        "success": True,
        "executed": False,
        "proposed_query": "Compare Alpha with Beta",
    }
    body = {"wording": "Compare it", "standalone_override": "Compare Alpha with Beta"}
    _cli_ns["cmd_followup"](client, argparse.Namespace(request=json.dumps(body)))
    client.followup_preview.assert_called_once_with(body)
    assert "Compare Alpha with Beta" in capsys.readouterr().out


def test_cli_dry_run_uses_existing_request_preview():
    client = _cli_ns["Client"](server="http://test", dry_run=True)
    result = client.followup_preview({"wording": "Explain Alpha"})
    assert result["dry_run"] is True
    assert result["method"] == "POST"
    assert result["url"].endswith("/v2/followup/preview")
