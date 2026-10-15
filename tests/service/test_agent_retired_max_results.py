"""Contract tests for retiring the agent-only per-query result cap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from agent.models import AgentRequest
from pydantic import ValidationError


def _load_cli():
    project_root = Path(__file__).resolve().parents[2]
    candidates = (project_root / "groktocrawl", project_root / "w6-fixtures" / "groktocrawl")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        rendered = ", ".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(f"Could not locate GroktoCrawl CLI; tried: {rendered}")
    namespace: dict = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    return namespace


def test_agent_request_rejects_retired_field_with_migration_message():
    assert "max_results_per_query" not in AgentRequest.model_json_schema()["properties"]
    with pytest.raises(ValidationError, match="agent now considers all distinct") as exc:
        AgentRequest(prompt="q", max_results_per_query=7)
    assert "omit max_results_per_query" in str(exc.value)


def test_agent_cli_client_omits_retired_field():
    cli = _load_cli()
    client = cli["Client"](server="http://test-server:8080", dry_run=False)
    client._request = MagicMock(return_value={"id": "job-1"})

    result = client.create_agent(prompt="q", search_type="focused")

    assert result == {"id": "job-1"}
    client._request.assert_called_once_with(
        "POST",
        "/agent",
        json_data={"prompt": "q", "search_type": "focused"},
        retry=False,
        operation="agent",
    )


def test_agent_cli_parser_has_no_retired_option():
    parser = _load_cli()["make_parser"]()
    agent_parser = next(
        action.choices["agent"]
        for action in parser._actions
        if getattr(action, "dest", "") == "command"
    )
    assert "--max-results-per-query" not in agent_parser._option_string_actions
