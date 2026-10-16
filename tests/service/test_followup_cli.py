"""CLI preview contract, including explicit override and dry-run behavior."""

import argparse
import json
from unittest.mock import MagicMock

import pytest

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
    assert result["url"] == "http://test/v2/followup/preview"
    execution = client.followup_execute({"wording": "Explain Alpha"}, "execution-1")
    assert execution["url"] == "http://test/v2/followup/execute"


def test_cli_explicit_execution_uses_idempotency_key():
    client = MagicMock()
    client.dry_run = False
    client.followup_execute.return_value = {"success": True, "run_id": "run-1"}
    body = {"wording": "Explain Alpha"}
    _cli_ns["cmd_followup"](
        client,
        argparse.Namespace(
            request=json.dumps(body), execute=True, idempotency_key="followup-1"
        ),
    )
    client.followup_execute.assert_called_once_with(body, "followup-1")
    client.followup_preview.assert_not_called()


def test_cli_execution_passes_webhook_and_evaluation_target():
    client = MagicMock(dry_run=False)
    client.followup_execute.return_value = {"success": True, "run_id": "new-run"}
    _cli_ns["cmd_followup"](
        client,
        argparse.Namespace(
            request='{"wording":"Explain Alpha"}',
            execute=True,
            idempotency_key="options-1",
            webhook="https://receiver.example/hook",
            as_of="2026-09-01T00:00:00Z",
        ),
    )
    client.followup_execute.assert_called_once_with(
        {
            "wording": "Explain Alpha",
            "webhook": "https://receiver.example/hook",
            "as_of": "2026-09-01T00:00:00Z",
        },
        "options-1",
    )


@pytest.mark.parametrize(
    "execute,body",
    [
        (False, {"wording": "Explain Alpha"}),
        (True, {"wording": "Explain Alpha", "webhook": "https://other.example/hook"}),
    ],
)
def test_cli_never_silently_drops_or_overrides_callback(execute, body):
    client = MagicMock(dry_run=False)
    with pytest.raises(SystemExit):
        _cli_ns["cmd_followup"](
            client,
            argparse.Namespace(
                request=json.dumps(body),
                execute=execute,
                idempotency_key="options-1",
                webhook="https://receiver.example/hook",
                as_of=None,
            ),
        )
    client.followup_execute.assert_not_called()
    client.followup_preview.assert_not_called()
