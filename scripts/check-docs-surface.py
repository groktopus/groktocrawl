#!/usr/bin/env python3
"""Validate checked-in documentation inventories against implementation sources.

The public-surface document deliberately tracks stable, reviewable names rather
than duplicating OpenAPI schemas. Run this after changing routes, the CLI,
Docker Compose services, or `.env.sample`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = REPO_ROOT / "agent-svc" / "agent" / "routes"
CLI_FILE = REPO_ROOT / "groktocrawl"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_SAMPLE = REPO_ROOT / ".env.sample"
INVENTORY_FILE = REPO_ROOT / "docs" / "reference" / "public-surface.md"


def between_markers(text: str, name: str) -> str:
    pattern = rf"<!-- {name}:start -->(.*?)<!-- {name}:end -->"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError(f"missing {name} markers")
    return match.group(1)


def route_inventory() -> set[str]:
    routes: set[str] = set()
    for source in ROUTES_DIR.glob("*.py"):
        if source.name.startswith("_"):
            continue
        for method, path in re.findall(
            r'@router\.(get|post|put|patch|delete)\s*\(\s*"([^"]+)"',
            source.read_text(),
        ):
            if path.startswith("/v2/"):
                routes.add(f"{method.upper()} {path}")
    return routes


def cli_inventory() -> set[str]:
    text = CLI_FILE.read_text()
    return set(re.findall(r'subparsers\.add_parser\(\s*"([a-z][a-z0-9-]*)"', text))


def service_inventory() -> set[str]:
    text = COMPOSE_FILE.read_text()
    # Only the top-level `volumes:` key ends the services mapping. Nested
    # service volume declarations must remain part of the scan.
    services = re.split(r"^volumes:\s*$", text, maxsplit=1, flags=re.MULTILINE)[0]
    return set(re.findall(r"^  ([a-z][a-z0-9-]+):$", services, re.MULTILINE))


def env_inventory() -> set[str]:
    return set(
        re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", ENV_SAMPLE.read_text(), re.MULTILINE)
    )


def documented_inventory(name: str, pattern: str) -> set[str]:
    block = between_markers(INVENTORY_FILE.read_text(), name)
    return set(re.findall(pattern, block, re.MULTILINE))


def compare(label: str, actual: set[str], documented: set[str]) -> list[str]:
    errors: list[str] = []
    missing = sorted(actual - documented)
    stale = sorted(documented - actual)
    if missing:
        errors.append(f"{label}: undocumented: {', '.join(missing)}")
    if stale:
        errors.append(f"{label}: documented but absent: {', '.join(stale)}")
    return errors


def main() -> int:
    errors: list[str] = []
    try:
        errors += compare(
            "API routes",
            route_inventory(),
            documented_inventory(
                "api-inventory", r"^(?:GET|POST|PUT|PATCH|DELETE) /v2/[^\s]+$"
            ),
        )
        errors += compare(
            "CLI commands",
            cli_inventory(),
            documented_inventory("cli-inventory", r"^- ([a-z][a-z0-9-]+)$"),
        )
        errors += compare(
            "Compose services",
            service_inventory(),
            documented_inventory("service-inventory", r"^- ([a-z][a-z0-9-]+)$"),
        )
        errors += compare(
            "Configuration keys",
            env_inventory(),
            documented_inventory("env-inventory", r"^- ([A-Z][A-Z0-9_]+)$"),
        )
    except ValueError as exc:
        errors.append(f"inventory format: {exc}")

    if errors:
        print("Documentation surface check failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(
        "Documentation surface inventory matches routes, CLI, Compose, and .env.sample."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
