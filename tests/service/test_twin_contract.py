"""Docker-free contracts for classifier, provenance, and calibration bounds."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import validate

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _isolate_twin_environment(monkeypatch):
    for name in (
        "TWIN_BASE_SHA",
        "TWIN_EVENT_HEAD_SHA",
        "TWIN_EVENT_KIND",
        "GITHUB_EVENT_NAME",
        "TWIN_SEARCH_BACKEND",
        "TWIN_BUILT_FROM_CHECKOUT",
        "TWIN_CALIBRATION_ARTIFACT",
        "TWIN_CHECKS",
        "TWIN_EXECUTION_MODE",
        "TWIN_EXCLUDED_TESTS",
        "TWIN_FAILURE_DETAIL",
        "TWIN_FAILURE_SOURCE",
        "TWIN_RECORD_IMAGES",
        "TWIN_REQUIRE_IMAGES",
        "TWIN_RESULT",
        "TWIN_SELECTION",
        "TWIN_TESTS",
        "TWIN_TEST_OUTCOME",
    ):
        monkeypatch.delenv(name, raising=False)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _advanced_base_and_pr_merge(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Twin contract test")
    _git(repo, "config", "user.email", "twin-contract@example.invalid")
    (repo / "shared.md").write_text("base version\n")
    _git(repo, "add", "shared.md")
    _git(repo, "commit", "--quiet", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "--quiet", "-b", "advanced-base")
    (repo / "shared.md").write_text("advanced base version\n")
    _git(repo, "commit", "--quiet", "-am", "advance base")
    event_base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "--quiet", "-b", "pr-head", base)
    (repo / "shared.md").write_text("pull request version\n")
    (repo / "pr-only.txt").write_text("pull request addition\n")
    _git(repo, "add", "shared.md", "pr-only.txt")
    _git(repo, "commit", "--quiet", "-m", "PR changes")
    event_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "--quiet", "advanced-base")
    merge = subprocess.run(
        ["git", "merge", "--no-commit", "pr-head"],
        cwd=repo,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert merge.returncode != 0  # The shared-file conflict is intentional.
    (repo / "shared.md").write_text("advanced base version\n")
    _git(repo, "add", "shared.md", "pr-only.txt")
    _git(repo, "commit", "--quiet", "-m", "synthetic PR merge")
    return repo, event_base, event_head, _git(repo, "rev-parse", "HEAD")


def test_twin_selection_is_conservative_and_docs_only_is_negative():
    classifier = _load("classifier", ROOT / "scripts/classify_ci_changes.py")
    assert classifier.requires_twin_contracts(["llm-svc/llm_svc/app.py"])
    assert classifier.requires_twin_contracts(["agent-svc/agent/llm.py"])
    assert classifier.requires_twin_contracts(["docker-compose.yml"])
    assert classifier.requires_twin_contracts(["unknown/new-policy.toml"])
    assert classifier.requires_twin_contracts([])
    assert not classifier.requires_twin_contracts(["docs/ci.md"])


def test_twin_evidence_schema_records_event_and_checked_out_revisions():
    schema = json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text())
    assert "event_diff" not in schema["required"]
    assert schema["properties"]["event_diff"]["required"] == [
        "event",
        "base_sha",
        "head_sha",
        "comparison_base_sha",
        "checked_out_sha",
    ]


def test_provenance_manifest_matches_versioned_schema(tmp_path):
    provenance = _load("provenance", ROOT / "scripts/twin_provenance.py")
    manifest = provenance.build_manifest(tmp_path, ["llm-svc/llm_svc/app.py"])
    schema = json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text())
    validate(manifest, schema)
    assert manifest["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert manifest["selection"]["inputs"] == ["llm-svc/llm_svc/app.py"]
    assert manifest["selection"]["tests"]
    assert manifest["versions"]["llm_schema"] == "v1"
    assert "prompt" not in json.dumps(manifest)


def test_historical_twin_evidence_v1_without_event_diff_still_validates(tmp_path):
    provenance = _load("historical_provenance", ROOT / "scripts/twin_provenance.py")
    manifest = provenance.build_manifest(tmp_path, ["llm-svc/llm_svc/app.py"])
    manifest.pop("event_diff")
    schema = json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text())
    validate(manifest, schema)


def test_pull_request_diff_uses_event_identity_when_base_advances(
    tmp_path, monkeypatch
):
    provenance = _load("event_diff_provenance", ROOT / "scripts/twin_provenance.py")
    repo, event_base, event_head, checked_out = _advanced_base_and_pr_merge(tmp_path)
    monkeypatch.setattr(provenance, "ROOT", repo)
    monkeypatch.setenv("TWIN_EVENT_KIND", "pull_request")
    monkeypatch.setenv("TWIN_BASE_SHA", event_base)
    monkeypatch.setenv("TWIN_EVENT_HEAD_SHA", event_head)

    expected = ["pr-only.txt", "shared.md"]
    assert provenance._actual_changed_paths("hosted") == expected
    assert _git(repo, "diff", "--name-only", event_base, checked_out) == "pr-only.txt"

    _, identity = provenance._event_diff_identity("hosted")
    assert identity == {
        "event": "pull_request",
        "base_sha": event_base,
        "head_sha": event_head,
        "comparison_base_sha": _git(repo, "merge-base", event_base, event_head),
        "checked_out_sha": checked_out,
    }


@pytest.mark.parametrize("event_head", ["", "not-a-commit-sha"])
def test_pull_request_diff_fails_closed_for_missing_or_invalid_head(
    tmp_path, monkeypatch, event_head
):
    provenance = _load("invalid_event_diff_provenance", ROOT / "scripts/twin_provenance.py")
    repo, event_base, _, _ = _advanced_base_and_pr_merge(tmp_path)
    monkeypatch.setattr(provenance, "ROOT", repo)
    monkeypatch.setenv("TWIN_EVENT_KIND", "pull_request")
    monkeypatch.setenv("TWIN_BASE_SHA", event_base)
    monkeypatch.setenv("TWIN_EVENT_HEAD_SHA", event_head)
    assert provenance._actual_changed_paths("hosted") is None


def test_pull_request_context_without_explicit_event_kind_or_head_fails_closed(
    tmp_path, monkeypatch
):
    provenance = _load("github_pr_context_provenance", ROOT / "scripts/twin_provenance.py")
    repo, event_base, _, _ = _advanced_base_and_pr_merge(tmp_path)
    monkeypatch.setattr(provenance, "ROOT", repo)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("TWIN_BASE_SHA", event_base)
    assert provenance._actual_changed_paths("hosted") is None
    _, identity = provenance._event_diff_identity("hosted")
    assert identity["event"] == "pull_request"
    assert identity["head_sha"] == "unavailable"


def test_pull_request_diff_fails_closed_for_non_ancestor_head(tmp_path, monkeypatch):
    provenance = _load("nonancestor_event_diff_provenance", ROOT / "scripts/twin_provenance.py")
    repo, event_base, _, checked_out = _advanced_base_and_pr_merge(tmp_path)
    tree = _git(repo, "rev-parse", f"{checked_out}^{{tree}}")
    unrelated_head = subprocess.check_output(
        ["git", "commit-tree", tree, "-m", "unrelated source"],
        cwd=repo,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    monkeypatch.setattr(provenance, "ROOT", repo)
    monkeypatch.setenv("TWIN_EVENT_KIND", "pull_request")
    monkeypatch.setenv("TWIN_BASE_SHA", event_base)
    monkeypatch.setenv("TWIN_EVENT_HEAD_SHA", unrelated_head)
    assert provenance._actual_changed_paths("hosted") is None


@pytest.mark.parametrize("root_event", [False, True])
def test_push_diff_uses_before_and_event_head_including_root_commit(
    tmp_path, monkeypatch, root_event
):
    provenance = _load("push_event_diff_provenance", ROOT / "scripts/twin_provenance.py")
    repo = tmp_path / "push-repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Twin contract test")
    _git(repo, "config", "user.email", "twin-contract@example.invalid")
    (repo / "one.txt").write_text("one\n")
    _git(repo, "add", "one.txt")
    _git(repo, "commit", "--quiet", "-m", "first")
    first = _git(repo, "rev-parse", "HEAD")
    if root_event:
        before = "0" * 40
        expected = ["one.txt"]
    else:
        before = first
        (repo / "two.txt").write_text("two\n")
        _git(repo, "add", "two.txt")
        _git(repo, "commit", "--quiet", "-m", "second")
        expected = ["two.txt"]
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(provenance, "ROOT", repo)
    monkeypatch.setenv("TWIN_EVENT_KIND", "push")
    monkeypatch.setenv("TWIN_BASE_SHA", before)
    monkeypatch.setenv("TWIN_EVENT_HEAD_SHA", head)
    assert provenance._actual_changed_paths("hosted") == expected


def test_invalid_provenance_paths_write_strict_failure_manifest(tmp_path, monkeypatch):
    provenance = _load("invalid_provenance", ROOT / "scripts/twin_provenance.py")
    monkeypatch.setenv("TWIN_RESULT", "success")
    output = tmp_path / "invalid"
    monkeypatch.setattr(
        "sys.argv",
        [
            "twin_provenance",
            "--output",
            str(output),
            "--changed-paths-json",
            "not-json",
        ],
    )
    assert provenance.main() == 1
    manifest = json.loads((output / "twin-evidence.json").read_text())
    schema = json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text())
    validate(manifest, schema)
    assert manifest["outcome"]["result"] == "failure"
    assert manifest["outcome"]["failure_source"] == "harness"


def test_hosted_manifest_rejects_selection_test_mismatch(tmp_path, monkeypatch):
    provenance = _load("mismatch_provenance", ROOT / "scripts/twin_provenance.py")
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("TWIN_BASE_SHA", provenance._git("rev-parse", "HEAD"))
    monkeypatch.setenv("TWIN_SELECTION", "search")
    monkeypatch.setenv("TWIN_TESTS", json.dumps(["tests/service/test_llm.py"]))
    monkeypatch.setattr(
        "sys.argv",
        ["twin_provenance", "--output", str(tmp_path), "--changed-paths-json", "[]"],
    )
    assert provenance.main() == 1
    manifest = json.loads((tmp_path / "twin-evidence.json").read_text())
    assert manifest["outcome"]["result"] == "failure"
    assert manifest["outcome"]["failure_source"] == "harness"
    assert "does not match" in manifest["outcome"]["failure_detail"]


def test_hosted_manifest_rejects_incomplete_changed_path_evidence(
    tmp_path, monkeypatch
):
    provenance = _load("path_mismatch_provenance", ROOT / "scripts/twin_provenance.py")
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("TWIN_SELECTION", "all")
    monkeypatch.setenv("TWIN_BASE_SHA", provenance._git("rev-parse", "HEAD"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "twin_provenance",
            "--output",
            str(tmp_path),
            "--changed-paths-json",
            json.dumps(["README.md"]),
        ],
    )
    assert provenance.main() == 1
    manifest = json.loads((tmp_path / "twin-evidence.json").read_text())
    assert manifest["outcome"]["result"] == "failure"
    assert manifest["outcome"]["failure_source"] == "harness"
    assert manifest["outcome"]["failure_detail"] == (
        "changed paths do not match checked-out event diff"
    )


def test_compose_unavailable_images_are_failure_not_acceptance(tmp_path, monkeypatch):
    provenance = _load("compose_provenance", ROOT / "scripts/twin_provenance.py")
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "compose")
    monkeypatch.setenv("TWIN_BASE_SHA", provenance._git("rev-parse", "HEAD"))
    monkeypatch.setenv("TWIN_REQUIRE_IMAGES", "1")
    monkeypatch.setenv("TWIN_RECORD_IMAGES", "1")
    monkeypatch.setattr(
        provenance,
        "_image_records",
        lambda mode: [
            {
                "name": name,
                "reference": name,
                "id": "unavailable",
                "repo_digest": None,
                "source_sha": "0" * 40,
                "built_from_checkout": False,
            }
            for name in provenance.REQUIRED_COMPOSE_IMAGES
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        ["twin_provenance", "--output", str(tmp_path), "--changed-paths-json", "[]"],
    )
    assert provenance.main() == 1
    manifest = json.loads((tmp_path / "twin-evidence.json").read_text())
    schema = json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text())
    validate(manifest, schema)
    assert manifest["outcome"]["failure_source"] == "infrastructure"


@pytest.mark.parametrize(
    "search_backend,valid_source",
    [("live", True), ("fixture", True), ("fixture", False)],
)
def test_compose_exact_images_validate_as_success(
    tmp_path, monkeypatch, search_backend, valid_source
):
    provenance = _load(
        "compose_success_provenance", ROOT / "scripts/twin_provenance.py"
    )
    checkout_sha = provenance._git("rev-parse", "HEAD")
    monkeypatch.setenv("TWIN_SEARCH_BACKEND", search_backend)
    source_images = provenance.REQUIRED_CHECKOUT_IMAGES.copy()
    digest_images = provenance.REQUIRED_REPO_DIGEST_IMAGES.copy()
    if search_backend == "fixture":
        digest_images.remove("slopsearx")
        if valid_source:
            source_images.add("slopsearx")
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "compose")
    monkeypatch.setenv("TWIN_BASE_SHA", provenance._git("rev-parse", "HEAD"))
    monkeypatch.setenv("TWIN_REQUIRE_IMAGES", "1")
    monkeypatch.setenv("TWIN_RECORD_IMAGES", "1")
    monkeypatch.setenv(
        "TWIN_BUILT_FROM_CHECKOUT", ",".join(provenance.REQUIRED_CHECKOUT_IMAGES)
    )
    monkeypatch.setenv(
        "TWIN_TESTS",
        json.dumps(
            [
                "tests/integration/test_stack.py::test_cross_endpoint_compact_citations_resolvable",
                "tests/integration/test_stack.py::test_cross_plan_agent_structured_output_val_cross_005",
                "tests/integration/test_critical_journey.py",
                "tests/integration/",
                "tests/service/",
                "tests/integration/test_twin_failure_injection.py",
                "mcp-svc/tests/test_integration.py::TestHostHeaderTransportSecurity",
            ]
        ),
    )
    monkeypatch.setenv(
        "TWIN_EXCLUDED_TESTS",
        json.dumps(
            [
                "tests/service/test_twin_contract.py",
                "tests/service/test_twin_network_isolation.py",
                "tests/service/test_workflow_contract.py",
                "tests/service/test_answer_evals.py",
            ]
        ),
    )
    monkeypatch.setattr(
        provenance,
        "_image_records",
        lambda mode: [
            {
                "name": name,
                "reference": name,
                "id": "sha256:" + "1" * 64,
                "repo_digest": (
                    f"example/{name}@sha256:" + "2" * 64
                    if name in digest_images
                    else None
                ),
                "source_sha": (checkout_sha if name in source_images else None),
                "built_from_checkout": name in source_images,
            }
            for name in provenance.REQUIRED_COMPOSE_IMAGES
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        ["twin_provenance", "--output", str(tmp_path), "--changed-paths-json", "[]"],
    )
    assert provenance.main() == (0 if valid_source else 1)
    manifest = json.loads((tmp_path / "twin-evidence.json").read_text())
    validate(
        manifest,
        json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text()),
    )
    assert manifest["execution_mode"] == "compose"
    assert len(manifest["images"]) == len(provenance.REQUIRED_COMPOSE_IMAGES)
    assert manifest["outcome"]["result"] == ("success" if valid_source else "failure")
    if not valid_source:
        assert "slopsearx" in manifest["outcome"]["failure_detail"]


def test_live_calibration_projects_into_strict_aggregate_manifest(
    tmp_path, monkeypatch
):
    provenance = _load("live_provenance", ROOT / "scripts/twin_provenance.py")
    calibration = {
        "schema_version": "live-calibration-v2",
        "outcome": "advisory_success",
        "failure_source": "provider_drift",
        "corpus": {"id": "live-v2", "digest": "3" * 64},
        "identity": {
            "requested": {"provider": "brave-search-api+llm", "model": "model-a"},
            "observed": {
                "search_provider": "brave-search-api",
                "llm_provider": "vendor-a",
                "model": "model-a",
            },
        },
        "limits": {
            "timeout_seconds": 20,
            "max_calls": 6,
            "max_tokens": 128,
            "max_total_tokens": 512,
        },
        "bounds": {
            "estimated_max_cost_usd": 0.1,
            "estimated_live_provider_cost_usd": 0.1,
            "cost_ceiling_usd": 1.0,
            "expected_calls": 4,
        },
        "records": [
            {
                "case_id": "s",
                "kind": "search",
                "result": "provider_drift",
                "classification": "provider_drift",
                "provider_fingerprint": "4" * 64,
                "twin_fingerprint": "5" * 64,
                "provider_latency_band": "fast",
                "twin_latency_band": "fast",
            }
        ],
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration))
    output = tmp_path / "aggregate"
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "live")
    monkeypatch.setenv("TWIN_SELECTION", "all")
    monkeypatch.setenv("TWIN_TESTS", json.dumps(["scripts/live_calibration.py"]))
    monkeypatch.setenv(
        "TWIN_CHECKS",
        "scenario-version-precheck,manifest-validation,fixture-immutability-check",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "twin_provenance",
            "--output",
            str(output),
            "--calibration-artifact",
            str(calibration_path),
        ],
    )
    assert provenance.main() == 0
    manifest = json.loads((output / "twin-evidence.json").read_text())
    validate(
        manifest,
        json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text()),
    )
    assert manifest["outcome"]["result"] == "advisory_success"
    assert manifest["outcome"]["failure_source"] == "provider_drift"
    assert manifest["corpus"] == calibration["corpus"]
    assert manifest["selection"]["tests"] == ["scripts/live_calibration.py"]
    assert manifest["identity"]["observed"]["provider"] == ("brave-search-api+vendor-a")


def test_missing_live_calibration_writes_valid_failure_manifest(tmp_path, monkeypatch):
    provenance = _load("missing_live_provenance", ROOT / "scripts/twin_provenance.py")
    output = tmp_path / "aggregate"
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "live")
    monkeypatch.setenv("TWIN_SELECTION", "all")
    monkeypatch.setenv("TWIN_TESTS", json.dumps(["scripts/live_calibration.py"]))
    monkeypatch.setattr(
        "sys.argv",
        [
            "twin_provenance",
            "--output",
            str(output),
            "--calibration-artifact",
            str(tmp_path / "missing.json"),
        ],
    )
    assert provenance.main() == 1
    manifest = json.loads((output / "twin-evidence.json").read_text())
    validate(
        manifest,
        json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text()),
    )
    assert manifest["outcome"]["result"] == "failure"
    assert manifest["outcome"]["failure_source"] == "harness"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "live-calibration-v2",
            "outcome": "failure",
            "failure_source": "harness",
            "records": [],
        },
        {
            "schema_version": "live-calibration-v2",
            "outcome": "success",
            "failure_source": "none",
            "corpus": {"id": "live-v2", "digest": "1" * 64},
            "identity": {
                "requested": {"provider": "provider", "model": "model"},
                "observed": {
                    "search_provider": "search",
                    "llm_provider": "llm",
                    "model": "model",
                },
            },
            "bounds": {
                "estimated_max_cost_usd": 0.1,
                "estimated_live_provider_cost_usd": 0.1,
                "cost_ceiling_usd": 1.0,
                "expected_calls": 2,
            },
            "limits": {
                "timeout_seconds": 20,
                "max_calls": 6,
                "max_tokens": 128,
                "max_total_tokens": 512,
            },
            "records": [{}],
        },
    ],
)
def test_incomplete_live_calibration_writes_valid_failure_manifest(
    tmp_path, monkeypatch, payload
):
    provenance = _load(
        "incomplete_live_provenance", ROOT / "scripts/twin_provenance.py"
    )
    artifact = tmp_path / "incomplete.json"
    artifact.write_text(json.dumps(payload))
    output = tmp_path / "aggregate"
    monkeypatch.setenv("TWIN_EXECUTION_MODE", "live")
    monkeypatch.setenv("TWIN_SELECTION", "all")
    monkeypatch.setenv("TWIN_TESTS", json.dumps(["scripts/live_calibration.py"]))
    monkeypatch.setenv(
        "TWIN_CHECKS",
        "scenario-version-precheck,manifest-validation,fixture-immutability-check",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "twin_provenance",
            "--output",
            str(output),
            "--calibration-artifact",
            str(artifact),
        ],
    )
    assert provenance.main() == 1
    manifest = json.loads((output / "twin-evidence.json").read_text())
    validate(
        manifest,
        json.loads((ROOT / "provenance/twin-evidence.schema.json").read_text()),
    )
    assert manifest["outcome"]["result"] == "failure"
    assert manifest["outcome"]["failure_source"] == "harness"
    assert manifest["outcome"]["failure_detail"] == (
        "missing or invalid calibration artifact"
    )


def test_calibration_failure_taxonomy_and_bounds():
    calibration = _load("calibration", ROOT / "scripts/live_calibration.py")
    assert calibration.classify_failure(401) == "authentication"
    assert calibration.classify_failure(429) == "quota"
    assert calibration.classify_failure(None) == "infrastructure"
    assert (
        calibration.classify_observation(200, 200, "{a:string}", "{a:string}")
        == "match"
    )
    assert (
        calibration.classify_observation(200, 200, "{a:string}", "{b:string}")
        == "provider_drift"
    )
    assert calibration.classify_observation(200, 500) == "twin"
    assert calibration.classify_observation(500, 200) == "infrastructure"
    assert calibration.classify_observation(401, 200) == "authentication"
    assert calibration.classify_observation(429, 200) == "quota"
    assert calibration.classify_observation(None, 200) == "infrastructure"
    assert calibration.classify_observation(200, 200, harness_error=True) == "harness"
    assert calibration.MAX_CALLS <= 6
    assert calibration.REQUEST_TIMEOUT_SECONDS <= 20
    assert calibration.MAX_RETRIES == 0
    assert calibration.MAX_TOKENS <= 128
    assert calibration.COST_CEILING_USD <= 1


def test_malformed_twin_response_is_twin_failure(tmp_path, monkeypatch):
    calibration = _load(
        "malformed_twin_calibration", ROOT / "scripts/live_calibration.py"
    )
    _set_calibration_env(monkeypatch)
    corpus = tmp_path / "corpus.json"
    _write_calibration_corpus(corpus)

    def request(url, method, headers, payload):
        if "twin" in url:
            return 200, {"malformed": True}
        return 200, {"results": [{"url": "u", "title": "t", "description": "d"}]}

    assert calibration.run(tmp_path / "out", corpus, request_fn=request) == 1
    artifact = json.loads((tmp_path / "out/calibration.json").read_text())
    assert artifact["records"][0]["result"] == "twin"
    assert artifact["failure_source"] == "twin"


def test_calibration_synthetic_outcomes_are_sanitized_and_bounded(
    tmp_path, monkeypatch
):
    calibration = _load("calibration_run", ROOT / "scripts/live_calibration.py")
    for key, value in {
        "BRAVE_API_KEY": "brave-secret",
        "LLM_API_KEY": "llm-secret",
        "LLM_BASE_URL": "https://provider.test/v1",
        "LLM_MODEL": "model-a",
        "BRAVE_COST_PER_CALL_USD": "0.01",
        "LLM_COST_PER_1K_TOKENS_USD": "0.01",
        "CALIBRATION_PROVIDER_SEARCH_URL": "https://provider.test/search",
        "CALIBRATION_TWIN_SEARCH_URL": "http://twin/search",
        "CALIBRATION_PROVIDER_URL": "https://provider.test/v1/chat/completions",
        "CALIBRATION_TWIN_LLM_URL": "http://twin/v1/chat/completions",
    }.items():
        monkeypatch.setenv(key, value)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "id": "synthetic",
                "cases": [
                    {"id": "s", "kind": "search", "input": "private query"},
                    {"id": "l", "kind": "llm", "input": "private prompt"},
                ],
            }
        )
    )
    outcomes = {
        "search": (200, {"results": [{"url": "u", "title": "x", "content": "c"}]}),
        "llm": (200, {"model": "model-a", "choices": []}),
    }

    def fake(url, method, headers, payload):
        kind = "search" if "search" in url else "llm"
        if "X-Subscription-Token" in headers:
            assert headers["X-Subscription-Token"] == "brave-secret"
        if kind == "llm":
            assert headers["Authorization"] == "Bearer llm-secret"
            assert payload["max_tokens"] == 128
        return outcomes[kind]

    assert calibration.run(tmp_path / "out", corpus, request_fn=fake) == 0
    artifact = json.loads((tmp_path / "out/calibration.json").read_text())
    assert [record["case_id"] for record in artifact["records"]] == ["s", "l"]
    assert "private" not in json.dumps(artifact)
    assert artifact["calls"] == 4

    def status(status):
        return lambda url, method, headers, payload: (status, {"error": "x"})

    for status_code, expected in [
        (401, "authentication"),
        (429, "quota"),
        (503, "infrastructure"),
    ]:
        artifact_dir = tmp_path / f"out-{status_code}"
        assert (
            calibration.run(artifact_dir, corpus, request_fn=status(status_code)) == 1
        )
        record = json.loads((artifact_dir / "calibration.json").read_text())["records"][
            0
        ]
        assert record["result"] == expected
        assert (
            json.loads((artifact_dir / "calibration.json").read_text())[
                "failure_source"
            ]
            == expected
        )

    for exception, expected in [
        (TimeoutError(), "infrastructure"),
        (ValueError("bad response"), "harness"),
    ]:
        artifact_dir = tmp_path / f"out-{expected}"

        def failing_request(*args, error=exception):
            raise error

        assert calibration.run(artifact_dir, corpus, request_fn=failing_request) == 1
        record = json.loads((artifact_dir / "calibration.json").read_text())["records"][
            0
        ]
        assert record["result"] == expected


def test_normalized_contracts_and_success_aggregation(tmp_path, monkeypatch):
    calibration = _load("normalized_calibration", ROOT / "scripts/live_calibration.py")
    for key, value in {
        "BRAVE_API_KEY": "key",
        "LLM_API_KEY": "key",
        "LLM_BASE_URL": "https://provider.test/v1",
        "LLM_MODEL": "requested",
        "BRAVE_COST_PER_CALL_USD": "0",
        "LLM_COST_PER_1K_TOKENS_USD": "0",
        "CALIBRATION_PROVIDER_SEARCH_URL": "https://provider.test/search",
        "CALIBRATION_TWIN_SEARCH_URL": "http://twin/search",
        "CALIBRATION_PROVIDER_URL": "https://provider.test/chat",
        "CALIBRATION_TWIN_LLM_URL": "http://twin/chat",
    }.items():
        monkeypatch.setenv(key, value)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "id": "c",
                "cases": [{"id": "s", "kind": "search", "input": "secret query"}],
            }
        )
    )

    def request(url, method, headers, payload):
        if "search" in url:
            if "provider.test" in url:
                return 200, {
                    "web": {"results": [{"url": "u", "title": "t", "description": "d"}]}
                }
            return 200, {
                "results": [{"url": "u", "title": "t", "content": "d", "engine": "x"}]
            }
        return 200, {
            "model": "observed",
            "choices": [
                {"message": {"content": "secret response"}, "finish_reason": "stop"}
            ],
        }

    assert calibration.run(tmp_path / "out", corpus, request_fn=request) == 0
    artifact = json.loads((tmp_path / "out/calibration.json").read_text())
    assert artifact["outcome"] == "success"
    assert artifact["failure_source"] == "none"
    assert (
        artifact["records"][0]["provider_fingerprint"]
        == artifact["records"][0]["twin_fingerprint"]
    )
    assert artifact["records"][0]["provider_latency_band"] in {"fast", "normal", "slow"}
    assert "secret" not in json.dumps(artifact)


def _set_calibration_env(monkeypatch, *, brave_cost="0.01", llm_cost="0.01"):
    for key, value in {
        "BRAVE_API_KEY": "brave-secret",
        "LLM_API_KEY": "llm-secret",
        "LLM_BASE_URL": "https://provider.test/v1",
        "LLM_MODEL": "model-a",
        "BRAVE_COST_PER_CALL_USD": brave_cost,
        "LLM_COST_PER_1K_TOKENS_USD": llm_cost,
        "CALIBRATION_PROVIDER_SEARCH_URL": "https://provider.test/search",
        "CALIBRATION_TWIN_SEARCH_URL": "http://twin/search",
        "CALIBRATION_PROVIDER_URL": "https://provider.test/chat",
        "CALIBRATION_TWIN_LLM_URL": "http://twin/chat",
    }.items():
        monkeypatch.setenv(key, value)


def _write_calibration_corpus(path, *, include_llm=False):
    cases = [{"id": "s", "kind": "search", "input": "private query"}]
    if include_llm:
        cases.append({"id": "l", "kind": "llm", "input": "private prompt"})
    path.write_text(json.dumps({"id": "synthetic", "cases": cases}))


def test_calibration_divergence_is_advisory_and_preserves_fixtures(
    tmp_path, monkeypatch
):
    calibration = _load("divergence_calibration", ROOT / "scripts/live_calibration.py")
    _set_calibration_env(monkeypatch)
    corpus = tmp_path / "corpus.json"
    _write_calibration_corpus(corpus)

    def request(url, method, headers, payload):
        if "provider.test" in url:
            return 200, {
                "web": {"results": [{"url": "u", "title": "t", "description": None}]}
            }
        return 200, {"results": [{"url": "u", "title": "t", "content": "d"}]}

    assert calibration.run(tmp_path / "out", corpus, request_fn=request) == 0
    artifact = json.loads((tmp_path / "out/calibration.json").read_text())
    assert artifact["outcome"] == "advisory_success"
    assert artifact["failure_source"] == "provider_drift"
    assert artifact["records"][0]["result"] == "provider_drift"
    assert (
        artifact["fixture_tree_digest_before"] == artifact["fixture_tree_digest_after"]
    )


def test_calibration_twin_failure_is_nonzero(tmp_path, monkeypatch):
    calibration = _load(
        "twin_failure_calibration", ROOT / "scripts/live_calibration.py"
    )
    _set_calibration_env(monkeypatch)
    corpus = tmp_path / "corpus.json"
    _write_calibration_corpus(corpus)

    def request(url, method, headers, payload):
        if "twin" in url:
            return 503, {"error": "fixture unavailable"}
        return 200, {
            "web": {"results": [{"url": "u", "title": "t", "description": "d"}]}
        }

    assert calibration.run(tmp_path / "out", corpus, request_fn=request) == 1
    artifact = json.loads((tmp_path / "out/calibration.json").read_text())
    assert artifact["outcome"] == "failure"
    assert artifact["failure_source"] == "twin"


def test_calibration_config_corpus_and_cost_fail_closed(tmp_path, monkeypatch):
    calibration = _load("fail_closed_calibration", ROOT / "scripts/live_calibration.py")
    corpus = tmp_path / "corpus.json"
    _write_calibration_corpus(corpus)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert (
        calibration.run(
            tmp_path / "missing", corpus, request_fn=lambda *args: (200, {})
        )
        == 1
    )
    missing = json.loads((tmp_path / "missing/calibration.json").read_text())
    assert missing["outcome"] == "failure"
    assert missing["failure_source"] == "harness"

    _set_calibration_env(monkeypatch, brave_cost="2.0")
    assert (
        calibration.run(tmp_path / "cost", corpus, request_fn=lambda *args: (200, {}))
        == 1
    )
    cost = json.loads((tmp_path / "cost/calibration.json").read_text())
    assert cost["outcome"] == "failure"
    assert cost["failure_source"] == "harness"

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"id": "bad", "cases": []}))
    assert (
        calibration.run(
            tmp_path / "corpus", invalid, request_fn=lambda *args: (200, {})
        )
        == 1
    )
    assert (
        json.loads((tmp_path / "corpus/calibration.json").read_text())["failure_source"]
        == "harness"
    )

    token_corpus = tmp_path / "token-corpus.json"
    token_corpus.write_text(
        json.dumps(
            {
                "id": "too-many-tokens",
                "cases": [
                    {"id": f"l{index}", "kind": "llm", "input": "bounded"}
                    for index in range(3)
                ],
            }
        )
    )
    _set_calibration_env(monkeypatch)
    assert (
        calibration.run(
            tmp_path / "tokens", token_corpus, request_fn=lambda *args: (200, {})
        )
        == 1
    )
    assert (
        json.loads((tmp_path / "tokens/calibration.json").read_text())["failure_source"]
        == "harness"
    )


def test_calibration_artifact_has_bounded_fingerprints_latency_and_identity(
    tmp_path, monkeypatch
):
    calibration = _load("identity_calibration", ROOT / "scripts/live_calibration.py")
    _set_calibration_env(monkeypatch)
    corpus = tmp_path / "corpus.json"
    _write_calibration_corpus(corpus, include_llm=True)

    def request(url, method, headers, payload):
        if "search" in url:
            if "provider.test" in url:
                return 200, {
                    "web": {"results": [{"url": "u", "title": "t", "description": "d"}]}
                }
            return 200, {"results": [{"url": "u", "title": "t", "content": "d"}]}
        return 200, {
            "provider": "vendor-a",
            "model": "model-a",
            "choices": [
                {"message": {"content": "private response"}, "finish_reason": "stop"}
            ],
        }

    assert calibration.run(tmp_path / "out", corpus, request_fn=request) == 0
    artifact = json.loads((tmp_path / "out/calibration.json").read_text())
    assert artifact["calls"] == 4 <= calibration.MAX_CALLS
    assert artifact["limits"]["max_tokens"] <= 128
    assert artifact["limits"]["max_total_tokens"] <= 512
    assert artifact["bounds"]["estimated_live_provider_cost_usd"] <= 1
    assert artifact["identity"]["observed"] == {
        "search_provider": "brave-search-api",
        "llm_provider": "vendor-a",
        "model": "model-a",
    }
    for record in artifact["records"]:
        for target in ("provider", "twin"):
            assert len(record[f"{target}_fingerprint"]) == 64
            assert record[f"{target}_latency_band"] in {"fast", "normal", "slow"}
    serialized = json.dumps(artifact)
    assert "private query" not in serialized
    assert "private prompt" not in serialized
    assert "private response" not in serialized
    assert "brave-secret" not in serialized
    assert "llm-secret" not in serialized
