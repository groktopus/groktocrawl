#!/usr/bin/env python3
"""Classify changed paths for CI runtime validation.

Pass paths as positional arguments, or provide one path per line on stdin when
no arguments are supplied.

Modes:
- default: prints ``true`` when full runtime validation is required and
  ``false`` only for a non-empty docs-only change.
- ``--affected-services``: prints the space-separated list of runtime service
  images that must be rebuilt for a pull request, or ``all`` when the change is
  cross-cutting/unrecognized and the full stack must be rebuilt.
- ``--requires-twin-contracts``: prints ``true`` when deterministic twin
  contracts are required.
- ``--twin-test-selection``: prints ``search``, ``llm``, ``all``, or ``none``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

DOCS_ONLY_FILES = frozenset({"README.md", "AGENTS.md", "CONTRIBUTING.md"})
DOCS_ONLY_PREFIXES = ("docs/", ".github/ISSUE_TEMPLATE/")

# Runtime service images in the build matrix / compose stack. Fixture services
# (llm-svc, test-site, tier3-fixture) are built by their own dedicated step.
RUNTIME_SERVICES = (
    "agent-svc",
    "scraper-svc",
    "browser-svc",
    "semantic-svc",
    "portal-svc",
    "parse-svc",
    "mcp-svc",
)

# Images used by the protected deployment override, validated in the required
# research-runtime workflow rather than the base Compose service-local lane.
PROTECTED_IMAGE_SERVICES = ("capture-egress-svc", "flare-control", "flare-protected")

# Paths whose change affects every service image.
_CROSS_CUTTING_PATHS = ("docker-compose.yml",)
_COMMON_PREFIX = "common/"
_TWIN_SHARED_PREFIXES = (
    "scenarios/",
    "scenario/",
    "tests/scenarios/",
    "tests/fixtures/",
    "provenance/",
    "scripts/twin_",
    "scripts/live_calibration",
)
_TWIN_EXACT_PATHS = frozenset(
    {
        "docker-compose.yml",
        ".github/workflows/docker.yml",
        ".github/workflows/runtime.yml",
        "docker-compose.ci.yml",
        ".github/workflows/live-calibration.yml",
    }
)


def _is_docs_only(path: str) -> bool:
    """Return whether a path is documentation-only.

    A path is docs-only when it is an allowlisted root-level prose file, sits
    under a docs-only prefix, or is markdown at the repo root or under
    ``.github/`` (e.g. ROADMAP.md, CHANGELOG.md, .github/PULL_REQUEST_TEMPLATE.md).
    Any other root-level file (e.g. requirements.txt) stays runtime-relevant and
    preserves the unknown-path escalation.
    """
    return (
        path in DOCS_ONLY_FILES
        or path.startswith(DOCS_ONLY_PREFIXES)
        or ("/" not in path and path.endswith(".md"))
        or (path.startswith(".github/") and path.endswith(".md"))
    )


def requires_full_runtime(paths: Iterable[str]) -> bool:
    """Return whether changed paths require the Docker integration runtime."""
    path_list = list(paths)
    if not path_list:
        return True

    return any(not _is_docs_only(path) for path in path_list)


def affected_services(paths: Iterable[str]) -> frozenset[str]:
    """Return the runtime services whose images must be rebuilt for a PR.

    Returns ``frozenset({"all"})`` when the change is cross-cutting, maps to no
    single service, or the path set is empty/malformed (conservative
    escalation). Returns an empty set for a pure docs-only change.
    """
    path_list = list(paths)
    if not path_list:
        return frozenset({"all"})

    affected: set[str] = set()
    for path in path_list:
        if not path.strip():
            return frozenset({"all"})
        if path in _CROSS_CUTTING_PATHS or path.startswith(_COMMON_PREFIX):
            return frozenset({"all"})
        matched = next(
            (svc for svc in RUNTIME_SERVICES if path.startswith(f"{svc}/")), None
        )
        if matched is not None:
            affected.add(matched)
            continue
        # Path is not under a known service dir. If it is runtime-relevant at
        # all (i.e. not docs-only), escalate to a full rebuild.
        if not _is_docs_only(path):
            return frozenset({"all"})

    return frozenset(affected)


def twin_test_selection(paths: Iterable[str]) -> str:
    """Select the narrow twin lane, escalating mixed/unknown changes to all."""
    path_list = list(paths)
    if not path_list or any(not path.strip() for path in path_list):
        return "all"
    relevant = [path for path in path_list if not _is_docs_only(path)]
    if not relevant:
        return "none"
    if any(
        path in _TWIN_EXACT_PATHS or path.startswith(_TWIN_SHARED_PREFIXES)
        for path in relevant
    ):
        return "all"
    search = any(
        path.startswith(
            ("slopsearx-fixture/", "tests/integration/test_slopsearx_fixture.py")
        )
        or path.endswith("/searxng_client.py")
        for path in relevant
    )
    llm = any(
        path.startswith("llm-svc/")
        or path.endswith("/llm.py")
        or path
        in {"tests/service/test_llm_fixture_contract.py", "tests/service/test_llm.py"}
        for path in relevant
    )
    if search and llm:
        return "all"
    if search:
        return "search"
    if llm:
        return "llm"
    return "all"


def requires_twin_contracts(paths: Iterable[str]) -> bool:
    return twin_test_selection(paths) != "none"


def main(argv: list[str]) -> int:
    if "--affected-services" in argv:
        args = [a for a in argv if a != "--affected-services"]
        paths = args or sys.stdin.read().splitlines()
        print(" ".join(sorted(affected_services(paths))))
        return 0

    if "--requires-twin-contracts" in argv or "--twin-test-selection" in argv:
        args = [
            a
            for a in argv
            if a not in {"--requires-twin-contracts", "--twin-test-selection"}
        ]
        paths = args or sys.stdin.read().splitlines()
        if "--requires-twin-contracts" in argv:
            print(str(requires_twin_contracts(paths)).lower())
        else:
            print(twin_test_selection(paths))
        return 0

    paths = argv or sys.stdin.read().splitlines()
    print(str(requires_full_runtime(paths)).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
