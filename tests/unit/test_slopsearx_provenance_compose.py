from pathlib import Path

import yaml


def test_override_enables_only_receipt_grant() -> None:
    value = yaml.safe_load(Path("compose.slopsearx-provenance.yml").read_text())
    assert "@sha256:" in value["services"]["slopsearx-mcp"]["image"]
    environment = value["services"]["slopsearx-mcp"]["environment"]
    assert environment["MCP_GRANT_RETRIEVAL_RECEIPTS"] == "1"
    assert environment["MCP_SNAPSHOT_TTL_SECONDS"].endswith(":-3600}")
    assert all(
        setting == "0"
        for name, setting in environment.items()
        if name.startswith("MCP_GRANT_") and name != "MCP_GRANT_RETRIEVAL_RECEIPTS"
    )
    agent = value["services"]["agent-svc"]["environment"]
    assert agent["SLOPSEARX_PROVENANCE_PROFILE"] == "retrieval-provenance-v1"
    assert agent["SLOPSEARX_PROVENANCE_MCP_URL"].endswith("/mcp")
