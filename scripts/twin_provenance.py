#!/usr/bin/env python3
"""Write the versioned, redacted aggregate evidence manifest for twin lanes."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

SCHEMA_VERSION = "twin-evidence-v1"
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_COMPOSE_IMAGES = (
    "llm-svc",
    "slopsearx-fixture",
    "agent-svc-fixture",
    "slopsearx",
    "test-site",
    "tier3-fixture",
    "scraper-svc",
    "semantic-svc",
    "qdrant",
    "valkey",
    "browser-svc",
    "parse-svc",
    "portal-svc",
    "mcp-svc",
    "agent-svc",
)
REQUIRED_REPO_DIGEST_IMAGES = {"slopsearx", "qdrant"}
REQUIRED_CHECKOUT_IMAGES = {
    "llm-svc",
    "slopsearx-fixture",
    "agent-svc-fixture",
    "test-site",
    "tier3-fixture",
}
ALLOWED_TESTS = {
    "tests/service/test_twin_contract.py",
    "tests/service/test_twin_network_isolation.py",
    "tests/service/test_workflow_contract.py",
    "tests/service/test_answer_evals.py",
    "tests/service/test_slopsearx_fixture.py",
    "tests/service/test_searxng_client.py",
    "tests/service/test_llm_fixture_contract.py",
    "tests/service/test_llm.py",
    "tests/service/test_research_adapter_parity.py",
    "tests/integration/test_stack.py",
    "tests/integration/test_stack.py::test_cross_endpoint_compact_citations_resolvable",
    "tests/integration/test_stack.py::test_cross_plan_agent_structured_output_val_cross_005",
    "tests/integration/test_critical_journey.py",
    "tests/integration/test_twin_failure_injection.py",
    "tests/integration/",
    "tests/service/",
    "mcp-svc/tests/test_integration.py::TestHostHeaderTransportSecurity",
    "scripts/live_calibration.py",
}
ALLOWED_CHECKS = {
    "scenario-version-precheck",
    "manifest-validation",
    "fixture-immutability-check",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _known_repo_path(path: str) -> bool:
    if not path or Path(path).is_absolute() or ".." in Path(path).parts:
        return False
    try:
        subprocess.check_call(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return Path(path).exists()


def _actual_changed_paths(mode: str) -> list[str] | None:
    if mode == "live":
        return []
    base_sha = os.environ.get("TWIN_BASE_SHA", "")
    if len(base_sha) != 40 or any(char not in "0123456789abcdef" for char in base_sha):
        return None
    try:
        if base_sha == "0" * 40:
            output = subprocess.check_output(
                [
                    "git",
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ],
                cwd=ROOT,
                text=True,
            )
        else:
            output = subprocess.check_output(
                ["git", "diff", "--name-only", base_sha, "HEAD"],
                cwd=ROOT,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(line for line in output.splitlines() if line)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _image_records(execution_mode: str) -> list[dict[str, object]]:
    if execution_mode in {"hosted", "live"} and not os.environ.get(
        "TWIN_RECORD_IMAGES"
    ):
        return []
    records = []
    references = {
        name: os.environ.get(
            f"TWIN_IMAGE_{name.upper().replace('-', '_')}",
            "ghcr.io/magnus919/slopsearx@sha256:0f89531014aa9692a555a8549d63f5e38a1f3d6fc56b6512f1af394574ba9c30"
            if name == "slopsearx"
            else name,
        )
        for name in REQUIRED_COMPOSE_IMAGES
    }
    source_sha = _git("rev-parse", "HEAD")
    built_services = set(
        filter(None, os.environ.get("TWIN_BUILT_FROM_CHECKOUT", "").split(","))
    )
    for name, default_reference in references.items():
        reference = os.environ.get(
            f"TWIN_IMAGE_{name.upper().replace('-', '_')}", default_reference
        )
        image_id = "unavailable"
        repo_digest = None
        try:
            service_id = subprocess.check_output(
                ["docker", "compose", "images", "-q", name], text=True
            ).strip()
            if service_id:
                reference = service_id
            image_id = subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
                text=True,
            ).strip()
            digests = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .RepoDigests}}",
                    reference,
                ],
                text=True,
            ).strip()
            parsed = json.loads(digests)
            repo_digest = parsed[0] if parsed else None
        except (
            OSError,
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            IndexError,
        ):
            pass
        records.append(
            {
                "name": name,
                "reference": reference,
                "id": image_id,
                "repo_digest": repo_digest,
                "source_sha": source_sha if name in built_services else None,
                "built_from_checkout": name in built_services,
            }
        )
    return records


def _fixture_versions() -> dict[str, str]:
    versions = {
        "scenario": "v0",
        "search_schema": "v0",
        "search_fixture": "v0",
        "llm_schema": "v0",
        "llm_fixture": "v0",
    }

    def constants(path: Path) -> dict[str, str]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return {}
        found: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign | ast.Assign):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) for target in targets):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "SCHEMA_VERSION",
                        "FIXTURE_VERSION",
                    }:
                        found[target.id] = node.value.value
        return found

    llm = constants(ROOT / "llm-svc/llm_svc/app.py")
    search = constants(ROOT / "slopsearx-fixture/slopsearx_fixture/app.py")
    versions.update(
        {
            "llm_schema": llm.get("SCHEMA_VERSION", "unavailable"),
            "llm_fixture": llm.get("FIXTURE_VERSION", "unavailable"),
            "search_schema": search.get("SCHEMA_VERSION", "unavailable"),
            "search_fixture": search.get("FIXTURE_VERSION", "unavailable"),
            "scenario": search.get("SCHEMA_VERSION", "unavailable"),
        }
    )
    return versions


TEST_SELECTIONS = {
    "search": [
        "tests/service/test_slopsearx_fixture.py",
        "tests/service/test_searxng_client.py",
    ],
    "llm": [
        "tests/service/test_llm_fixture_contract.py",
        "tests/service/test_llm.py",
    ],
    "all": [
        "tests/service/test_llm_fixture_contract.py",
        "tests/service/test_slopsearx_fixture.py",
        "tests/service/test_research_adapter_parity.py",
        "tests/service/test_searxng_client.py",
    ],
    "none": [],
}


def _selected_tests(selection: str) -> list[str]:
    explicit = os.environ.get("TWIN_TESTS")
    if explicit:
        try:
            parsed = json.loads(explicit)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return parsed
    selected = (
        parsed
        if explicit
        and isinstance(parsed, list)
        and all(isinstance(item, str) for item in parsed)
        else TEST_SELECTIONS.get(selection, [])
    )
    return list(
        dict.fromkeys(
            [
                *selected,
                "tests/service/test_twin_contract.py",
                "tests/service/test_twin_network_isolation.py",
                "tests/service/test_workflow_contract.py",
            ]
        )
    )


def _selected_checks() -> list[str]:
    raw = os.environ.get("TWIN_CHECKS", "scenario-version-precheck,manifest-validation")
    return list(dict.fromkeys(item for item in raw.split(",") if item))


def _excluded_tests() -> list[str]:
    raw = os.environ.get("TWIN_EXCLUDED_TESTS", "[]")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ["invalid"]
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return ["invalid"]
    return list(dict.fromkeys(parsed))


def _expected_hosted_tests(selection: str) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *TEST_SELECTIONS.get(selection, []),
                "tests/service/test_twin_contract.py",
                "tests/service/test_twin_network_isolation.py",
                "tests/service/test_workflow_contract.py",
            ]
        )
    )


def _selection_error(
    mode: str,
    selection: str,
    tests: list[str],
    excluded_tests: list[str],
    checks: list[str],
) -> str | None:
    if selection not in TEST_SELECTIONS:
        return "unknown twin selection"
    if any(test not in ALLOWED_TESTS for test in tests):
        return "unknown selected test"
    if any(test not in ALLOWED_TESTS for test in excluded_tests):
        return "unknown excluded test"
    if any(check not in ALLOWED_CHECKS for check in checks):
        return "unknown selected check"
    if mode == "hosted" and tests != _expected_hosted_tests(selection):
        return "hosted test selection does not match classifier selection"
    if mode in {"hosted", "live"} and excluded_tests:
        return f"{mode} test selection cannot exclude tests"
    if mode == "compose":
        required = {
            "tests/integration/test_stack.py::test_cross_endpoint_compact_citations_resolvable",
            "tests/integration/test_stack.py::test_cross_plan_agent_structured_output_val_cross_005",
            "tests/integration/test_critical_journey.py",
            "tests/integration/",
            "tests/service/",
            "tests/integration/test_twin_failure_injection.py",
            "mcp-svc/tests/test_integration.py::TestHostHeaderTransportSecurity",
        }
        if not required.issubset(tests):
            return "compose test selection is incomplete"
        expected_exclusions = {
            "tests/service/test_twin_contract.py",
            "tests/service/test_twin_network_isolation.py",
            "tests/service/test_workflow_contract.py",
            "tests/service/test_answer_evals.py",
        }
        if set(excluded_tests) != expected_exclusions:
            return "compose host-only exclusions are incomplete"
    if mode == "live" and tests != ["scripts/live_calibration.py"]:
        return "live test selection is not the calibration harness"
    required_checks = {"scenario-version-precheck", "manifest-validation"}
    if not required_checks.issubset(checks):
        return "required evidence checks are missing"
    if mode == "live" and "fixture-immutability-check" not in checks:
        return "live fixture immutability check is missing"
    return None


def _execution_mode() -> str:
    mode = os.environ.get("TWIN_EXECUTION_MODE", "hosted")
    return mode if mode in {"hosted", "compose", "live"} else "hosted"


def _valid_calibration_artifact(calibration: object) -> bool:
    if not isinstance(calibration, dict):
        return False
    corpus = calibration.get("corpus")
    identity = calibration.get("identity")
    bounds = calibration.get("bounds")
    limits = calibration.get("limits")
    records = calibration.get("records")
    if calibration.get("schema_version") != "live-calibration-v2":
        return False
    if calibration.get("outcome") not in {"success", "failure", "advisory_success"}:
        return False
    if calibration.get("failure_source") not in {
        "none",
        "authentication",
        "quota",
        "twin",
        "infrastructure",
        "harness",
        "provider_drift",
    }:
        return False
    if not (
        isinstance(corpus, dict)
        and isinstance(corpus.get("id"), str)
        and corpus["id"]
        and isinstance(corpus.get("digest"), str)
        and len(corpus["digest"]) == 64
        and all(char in "0123456789abcdef" for char in corpus["digest"])
    ):
        return False
    if not isinstance(identity, dict):
        return False
    requested = identity.get("requested")
    observed = identity.get("observed")
    if not (
        isinstance(requested, dict)
        and all(isinstance(requested.get(key), str) for key in ("provider", "model"))
        and isinstance(observed, dict)
        and all(
            isinstance(observed.get(key), str)
            for key in ("search_provider", "llm_provider", "model")
        )
    ):
        return False
    if not (
        isinstance(bounds, dict)
        and all(
            isinstance(bounds.get(key), int | float)
            for key in (
                "estimated_max_cost_usd",
                "estimated_live_provider_cost_usd",
                "cost_ceiling_usd",
                "expected_calls",
            )
        )
        and isinstance(limits, dict)
        and all(
            isinstance(limits.get(key), int | float)
            for key in (
                "timeout_seconds",
                "max_calls",
                "max_tokens",
                "max_total_tokens",
            )
        )
    ):
        return False
    if not isinstance(records, list):
        return False
    classifications = {
        "match",
        "provider_drift",
        "authentication",
        "quota",
        "twin",
        "infrastructure",
        "harness",
    }
    for record in records:
        if not isinstance(record, dict):
            return False
        if not isinstance(record.get("case_id"), str) or not record["case_id"]:
            return False
        if record.get("kind") not in {"search", "llm"}:
            return False
        if record.get("result") not in classifications:
            return False
        if record.get("classification") not in classifications:
            return False
        for key in ("provider_fingerprint", "twin_fingerprint"):
            value = record.get(key)
            if value is not None and not (
                value == "unavailable"
                or (
                    isinstance(value, str)
                    and len(value) == 64
                    and all(char in "0123456789abcdef" for char in value)
                )
            ):
                return False
        for key in ("provider_latency_band", "twin_latency_band"):
            value = record.get(key)
            if value is not None and value not in {"fast", "normal", "slow"}:
                return False
    return True


def build_manifest(output: Path, inputs: list[str] | None = None) -> dict[str, object]:
    corpus = Path("provenance/twin-corpus.json")
    corpus_data = json.loads(corpus.read_text()) if corpus.exists() else {}
    started = os.environ.get("TWIN_STARTED_AT", _now())
    requested_result = os.environ.get("TWIN_RESULT", "success")
    result = (
        requested_result
        if requested_result in {"success", "failure", "advisory_success"}
        else "failure"
    )
    requested_source = os.environ.get("TWIN_FAILURE_SOURCE", "harness")
    source = (
        requested_source
        if requested_source
        in {
            "none",
            "authentication",
            "quota",
            "twin",
            "infrastructure",
            "harness",
            "provider_drift",
            "implementation",
        }
        else "harness"
    )
    if result == "success":
        source = "none"
    mode = _execution_mode()
    selected_tests = _selected_tests(os.environ.get("TWIN_SELECTION", "all"))
    excluded_tests = _excluded_tests()
    selected_checks = _selected_checks()
    selection_error = _selection_error(
        mode,
        os.environ.get("TWIN_SELECTION", "all"),
        selected_tests,
        excluded_tests,
        selected_checks,
    )
    if selection_error:
        result = "failure"
        source = "harness"
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "execution_mode": mode,
        "commit": {
            "sha": _git("rev-parse", "HEAD"),
            "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
            "ref": os.environ.get("GITHUB_REF", "local"),
        },
        "selection": {
            "inputs": inputs or [],
            "twin": os.environ.get("TWIN_SELECTION", "all"),
            "runtime": os.environ.get("RUNTIME_SELECTION", "unknown"),
            "tests": selected_tests,
            "excluded_tests": excluded_tests,
            "checks": selected_checks,
        },
        "corpus": {
            "id": corpus_data.get("id", "unavailable"),
            "digest": _digest(corpus) if corpus.exists() else "unavailable",
        },
        "versions": _fixture_versions(),
        "identity": {
            "requested": {
                "provider": os.environ.get("LLM_PROVIDER", "fixture"),
                "model": os.environ.get("LLM_MODEL", "fixture-model"),
            },
            "observed": {
                "provider": os.environ.get("OBSERVED_PROVIDER", "unavailable"),
                "model": os.environ.get("OBSERVED_MODEL", "unavailable"),
            },
        },
        "images": _image_records(mode),
        "bounds": {
            "timeout_seconds": int(os.environ.get("TWIN_TIMEOUT_SECONDS", "300")),
            "max_calls": int(os.environ.get("TWIN_MAX_CALLS", "0")),
            "max_tokens": int(os.environ.get("TWIN_MAX_TOKENS", "0")),
            "cost_ceiling_usd": float(os.environ.get("TWIN_COST_CEILING_USD", "0")),
        },
        "outcome": {
            "result": result,
            "failure_source": source,
            "test_outcome": os.environ.get("TWIN_TEST_OUTCOME", "unavailable"),
            "failure_detail": selection_error
            or os.environ.get("TWIN_FAILURE_DETAIL", "unavailable"),
            "started_at": started,
            "finished_at": _now(),
        },
    }
    calibration_artifact = os.environ.get("TWIN_CALIBRATION_ARTIFACT")
    if calibration_artifact:
        try:
            calibration = json.loads(Path(calibration_artifact).read_text())
            if not _valid_calibration_artifact(calibration):
                raise ValueError("invalid calibration artifact")
            manifest["calibration"] = {
                "schema_version": calibration.get("schema_version"),
                "outcome": calibration.get("outcome", "failure"),
                "failure_source": calibration.get("failure_source", "harness"),
                "corpus": calibration.get("corpus", {}),
                "identity": calibration.get("identity", {}),
                "bounds": calibration.get("bounds", {}),
                "records": [
                    {
                        key: record.get(key)
                        for key in record
                        if key.endswith(("_fingerprint", "_latency_band"))
                        or key in {"case_id", "kind", "result", "classification"}
                    }
                    for record in calibration["records"]
                    if isinstance(record, dict)
                ],
            }
            calibration_corpus = calibration.get("corpus")
            if isinstance(calibration_corpus, dict):
                manifest["corpus"] = calibration_corpus
            calibration_bounds = calibration.get("bounds")
            calibration_limits = calibration.get("limits")
            if isinstance(calibration_bounds, dict) and isinstance(
                calibration_limits, dict
            ):
                manifest["bounds"] = {
                    "timeout_seconds": calibration_limits.get("timeout_seconds", 0),
                    "max_calls": calibration_limits.get("max_calls", 0),
                    "max_tokens": calibration_limits.get(
                        "max_total_tokens", calibration_limits.get("max_tokens", 0)
                    ),
                    "cost_ceiling_usd": calibration_bounds.get("cost_ceiling_usd", 0),
                }
            calibration_identity = calibration.get("identity")
            if isinstance(calibration_identity, dict):
                requested = calibration_identity.get("requested", {})
                observed = calibration_identity.get("observed", {})
                if isinstance(requested, dict) and isinstance(observed, dict):
                    observed_providers = [
                        observed.get("search_provider"),
                        observed.get("llm_provider"),
                    ]
                    manifest["identity"] = {
                        "requested": {
                            "provider": str(requested.get("provider", "unavailable")),
                            "model": str(requested.get("model", "unavailable")),
                        },
                        "observed": {
                            "provider": "+".join(
                                str(provider)
                                for provider in observed_providers
                                if provider and provider != "unavailable"
                            )
                            or "unavailable",
                            "model": str(observed.get("model", "unavailable")),
                        },
                    }
            calibration_outcome = calibration.get("outcome")
            calibration_source = calibration.get("failure_source")
            if calibration_outcome in {"success", "failure", "advisory_success"}:
                manifest["outcome"] = {
                    **cast(dict[str, object], manifest["outcome"]),
                    "result": calibration_outcome,
                    "failure_source": calibration_source
                    if calibration_source
                    in {
                        "none",
                        "authentication",
                        "quota",
                        "twin",
                        "infrastructure",
                        "harness",
                        "provider_drift",
                    }
                    else "harness",
                }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            os.environ["TWIN_CALIBRATION_INVALID"] = "1"
            manifest["calibration"] = {
                "schema_version": "live-calibration-v2",
                "outcome": "failure",
                "failure_source": "harness",
                "corpus": {"id": "unavailable", "digest": "0" * 64},
                "identity": {
                    "requested": {"provider": "unavailable", "model": "unavailable"},
                    "observed": {
                        "search_provider": "unavailable",
                        "llm_provider": "unavailable",
                        "model": "unavailable",
                    },
                },
                "bounds": {
                    "estimated_max_cost_usd": 0,
                    "estimated_live_provider_cost_usd": 0,
                    "cost_ceiling_usd": 0,
                    "expected_calls": 0,
                },
                "records": [],
            }
            manifest["outcome"] = {
                **cast(dict[str, object], manifest["outcome"]),
                "result": "failure",
                "failure_source": "harness",
                "failure_detail": "missing or invalid calibration artifact",
            }
    output.mkdir(parents=True, exist_ok=True)
    (output / "twin-evidence.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--changed-paths-json", default="[]")
    parser.add_argument("--calibration-artifact", type=Path)
    args = parser.parse_args()
    if args.calibration_artifact:
        os.environ["TWIN_CALIBRATION_ARTIFACT"] = str(args.calibration_artifact)
    invalid = False
    try:
        paths = json.loads(args.changed_paths_json)
        if not isinstance(paths, list) or not all(
            isinstance(path, str) for path in paths
        ):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        paths = []
        invalid = True
        os.environ["TWIN_RESULT"] = "failure"
        os.environ["TWIN_FAILURE_SOURCE"] = "harness"
    invalid_paths = [path for path in paths if not _known_repo_path(path)]
    if invalid_paths:
        invalid = True
        os.environ["TWIN_RESULT"] = "failure"
        os.environ["TWIN_FAILURE_SOURCE"] = "harness"
        os.environ["TWIN_FAILURE_DETAIL"] = "invalid changed paths"
    actual_paths = _actual_changed_paths(_execution_mode())
    if actual_paths is None or sorted(paths) != actual_paths:
        invalid = True
        os.environ["TWIN_RESULT"] = "failure"
        os.environ["TWIN_FAILURE_SOURCE"] = "harness"
        os.environ["TWIN_FAILURE_DETAIL"] = (
            "changed paths do not match checked-out event diff"
        )
    manifest = build_manifest(args.output, paths)
    if cast(dict[str, object], manifest["outcome"])["result"] == "failure":
        invalid = True
    if (
        os.environ.get("TWIN_EXECUTION_MODE") == "compose"
        and os.environ.get("TWIN_REQUIRE_IMAGES") == "1"
    ):
        images = cast(list[dict[str, object]], manifest["images"])
        missing = [
            str(record["name"])
            for record in images
            if record["id"] == "unavailable"
            or (
                record["name"] in REQUIRED_REPO_DIGEST_IMAGES
                and not record["repo_digest"]
            )
        ]
        checkout_sha = _git("rev-parse", "HEAD")
        missing.extend(
            str(record["name"])
            for record in images
            if record["name"] in REQUIRED_CHECKOUT_IMAGES
            and (
                not record["built_from_checkout"]
                or record["source_sha"] != checkout_sha
            )
        )
        missing = sorted(set(missing))
        if missing:
            invalid = True
            manifest["outcome"] = {
                **cast(dict[str, object], manifest["outcome"]),
                "result": "failure",
                "failure_source": "infrastructure",
                "failure_detail": "required images unavailable: " + ",".join(missing),
            }
            (args.output / "twin-evidence.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
    if args.calibration_artifact and (
        not Path(args.calibration_artifact).is_file()
        or os.environ.get("TWIN_CALIBRATION_INVALID") == "1"
    ):
        invalid = True
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
