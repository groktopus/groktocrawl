#!/usr/bin/env python3
"""Replace raw Docker stats with a small, identity-free W9 resource receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SERVICES = {
    "agent",
    "browser",
    "mcp",
    "parse",
    "portal",
    "postgres",
    "qdrant-rollback",
    "scraper",
    "semantic",
    "slopsearx",
    "valkey",
}
NAME_PATTERN = re.compile(
    r".+-candidate-(?P<service>agent|browser|mcp|parse|portal|postgres|"
    r"qdrant-rollback|scraper|semantic|slopsearx|valkey)-[0-9]+"
)
PERCENT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?%")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return value


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitize_resources(checkpoint_dir: Path, receipts: dict[str, Any]) -> None:
    resource_path = checkpoint_dir / "resources.json"
    resource = read_json(resource_path)
    if resource.get("schema_version") == "w9-resource-snapshot/1":
        if digest(resource_path) != receipts["resources.json"]:
            raise ValueError("sanitized resource receipt digest does not match")
        return

    original_digest = digest(resource_path)
    if receipts["resources.json"] != original_digest:
        raise ValueError("raw resource receipt digest does not match checkpoint")
    containers = resource.get("containers")
    if not isinstance(containers, list):
        raise ValueError("raw resource receipt has no containers list")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for container in containers:
        if not isinstance(container, dict):
            raise ValueError("raw resource receipt contains an invalid row")
        match = NAME_PATTERN.fullmatch(str(container.get("Name", "")))
        if match is None:
            raise ValueError("raw resource receipt contains an unknown service")
        service = match.group("service")
        cpu = str(container.get("CPUPerc", ""))
        memory = str(container.get("MemPerc", ""))
        if service in seen or service not in SERVICES:
            raise ValueError("raw resource receipt has duplicate or unknown services")
        if not PERCENT_PATTERN.fullmatch(cpu) or not PERCENT_PATTERN.fullmatch(memory):
            raise ValueError("raw resource receipt has invalid resource percentages")
        seen.add(service)
        normalized.append(
            {"service": service, "cpu_percent": cpu, "memory_percent": memory}
        )

    if seen != SERVICES:
        raise ValueError("raw resource receipt does not include the frozen service set")
    sanitized = {
        "schema_version": "w9-resource-snapshot/1",
        "observed_at": resource.get("observed_at"),
        "source_receipt_sha256": original_digest,
        "containers": sorted(normalized, key=lambda row: row["service"]),
    }
    write_json(resource_path, sanitized)
    receipts["resources.json"] = digest(resource_path)


def sanitize_research(checkpoint_dir: Path, receipts: dict[str, Any]) -> None:
    research_path = checkpoint_dir / "research.json"
    research = read_json(research_path)
    if research.get("publication_sanitization") == "w9-public-packet/1":
        if digest(research_path) != receipts["research.json"]:
            raise ValueError("sanitized research receipt digest does not match")
        return

    original_digest = digest(research_path)
    if receipts["research.json"] != original_digest:
        raise ValueError("raw research receipt digest does not match checkpoint")
    compose = research.get("compose")
    if not isinstance(compose, dict):
        raise ValueError("research receipt has no Compose summary")
    services = compose.get("services")
    images = compose.get("images")
    if not isinstance(services, list) or not isinstance(images, list):
        raise ValueError("research receipt has invalid Compose details")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in services:
        if not isinstance(record, dict):
            raise ValueError("research receipt contains an invalid service row")
        service = str(record.get("service", ""))
        if service.startswith("candidate-"):
            service = service.removeprefix("candidate-")
        state = str(record.get("state", ""))
        health = str(record.get("health", ""))
        if service not in SERVICES or service in seen:
            raise ValueError("research receipt has duplicate or unknown services")
        if state != "running" or health != "healthy":
            raise ValueError("research receipt contains an unhealthy service")
        seen.add(service)
        normalized.append({"service": service, "state": state, "health": health})
    if seen != SERVICES:
        raise ValueError("research receipt does not include the frozen service set")

    research["source_receipt_sha256"] = original_digest
    research["publication_sanitization"] = "w9-public-packet/1"
    research["compose"] = {
        "version": compose.get("version"),
        "services": sorted(normalized, key=lambda row: row["service"]),
        "image_count": len(images),
    }
    write_json(research_path, research)
    receipts["research.json"] = digest(research_path)


def sanitize(checkpoint_dir: Path) -> None:
    checkpoint_path = checkpoint_dir / "checkpoint.json"
    checkpoint = read_json(checkpoint_path)
    receipts = checkpoint.get("receipts")
    if not isinstance(receipts, dict):
        raise ValueError("checkpoint has no receipt digests")
    if "resources.json" not in receipts or "research.json" not in receipts:
        raise ValueError("checkpoint does not record the expected receipt digests")
    sanitize_resources(checkpoint_dir, receipts)
    sanitize_research(checkpoint_dir, receipts)
    write_json(checkpoint_path, checkpoint)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("checkpoint_dir", type=Path)
    return value


def main() -> None:
    args = parser().parse_args()
    sanitize(args.checkpoint_dir)


if __name__ == "__main__":
    main()
