#!/usr/bin/env python3
"""Prepare a private Compose environment and redacted manifest for a W11 arm."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
from typing import Any

SLOPSEARX_VERSION = "0.5.0"
SLOPSEARX_SOURCE_REVISION = "edeba9ef9311adf19ce3f1b6060257ef1b54c2f9"
SLOPSEARX_IMAGE_DIGEST = (
    "sha256:4cd9c04ae4ef2f154a104cd95cf2219d603e461c9449f4392ce562b0541dd2d3"
)

GRANTS = {
    "dependency_dossier": "MCP_GRANT_DEPENDENCY_DOSSIER",
    "research": "MCP_GRANT_RESEARCH",
    "retrieval_receipts": "MCP_GRANT_RETRIEVAL_RECEIPTS",
    "saved_search_events": "MCP_GRANT_SAVED_SEARCH_EVENTS",
    "saved_searches": "MCP_GRANT_SAVED_SEARCHES",
    "security": "MCP_GRANT_SECURITY",
    "staged_search": "MCP_GRANT_STAGED_SEARCH",
}
ARM_GRANTS = {
    "control": set(),
    "research": {"research"},
    "staged": {"staged_search"},
    "provenance": {"retrieval_receipts"},
    "evidence": {"retrieval_receipts", "staged_search"},
    # Representative artifact composition needs research plus manifest export.
    # Broader cross-workflow transitions remain isolated deterministic cases.
    "composition": {"research", "retrieval_receipts"},
    "saved": {"saved_search_events", "saved_searches"},
    # Dossiers compose package, repository, and advisory research and therefore
    # require the three grants together in SlopSearX 0.5.
    "dossier": {"dependency_dossier", "research", "security"},
}
ALWAYS_DISABLED = {
    "MCP_GRANT_JOBS": "0",
    "MCP_GRANT_SCIENCE": "0",
    "MCP_TARGETED_SENSITIVE_ALLOWED": "0",
}


def arm_environment(
    arm: str,
    *,
    token: str,
    brave_api_key: str,
    mcp_port: int,
    http_port: int,
) -> tuple[str, dict[str, object]]:
    if arm not in ARM_GRANTS:
        raise ValueError(f"unknown W11 arm: {arm}")
    if not token:
        raise ValueError("MCP token must not be empty")
    if not brave_api_key:
        raise ValueError("Brave API key must not be empty")
    for name, port in (("MCP", mcp_port), ("HTTP", http_port)):
        if not 1024 <= port <= 65535:
            raise ValueError(f"{name} port must be between 1024 and 65535")
    if mcp_port == http_port:
        raise ValueError("MCP and HTTP ports must differ")

    enabled = ARM_GRANTS[arm]
    values = {
        "BRAVE_API_KEY": brave_api_key,
        "SLOPSEARX_MCP_AUTH_TOKEN": token,
        "SLOPSEARX_MCP_PORT": str(mcp_port),
        "SLOPSEARX_HOST_PORT": str(http_port),
        **ALWAYS_DISABLED,
        **{env: "1" if grant in enabled else "0" for grant, env in GRANTS.items()},
    }
    content = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    manifest: dict[str, Any] = {
        "schema_version": "enterprise-evaluation/w11-arm/1",
        "arm": arm,
        "slopsearx": {
            "version": SLOPSEARX_VERSION,
            "source_revision": SLOPSEARX_SOURCE_REVISION,
            "image_digest": SLOPSEARX_IMAGE_DIGEST,
        },
        "isolation": {
            "dedicated_compose_project_required": True,
            "dedicated_network_required": True,
            "dedicated_valkey_volume_required": True,
            "unique_host_ports_required": True,
            "private_credential_present": True,
        },
        "grants": {
            "enabled": sorted(enabled),
            "disabled": sorted(set(GRANTS) - enabled),
            "jobs": False,
            "science": False,
            "targeted_sensitive": False,
        },
    }
    return content, manifest


def write_exclusive(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=sorted(ARM_GRANTS))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mcp-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--token-env", default="SLOPSEARX_MCP_AUTH_TOKEN")
    parser.add_argument("--brave-key-env", default="BRAVE_API_KEY")
    args = parser.parse_args()
    token = os.environ.get(args.token_env) or secrets.token_hex(32)
    content, manifest = arm_environment(
        args.arm,
        token=token,
        brave_api_key=os.environ.get(args.brave_key_env, ""),
        mcp_port=args.mcp_port,
        http_port=args.http_port,
    )
    write_exclusive(args.output_dir / "arm.env", content, mode=0o600)
    write_exclusive(
        args.output_dir / "arm-manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    print(f"prepared private W11 {args.arm} arm in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
