"""Contract checks for sanitized LLM fixture CI evidence."""

from pathlib import Path

import yaml


def test_docker_workflow_captures_and_uploads_sanitized_fixture_diagnostics():
    workflow = (Path(__file__).parents[2] / ".github/workflows/docker.yml").read_text()
    assert "Capture sanitized LLM fixture diagnostics" in workflow
    assert "llm-fixture-diagnostics.json" in workflow
    assert "Upload LLM fixture diagnostics" in workflow
    assert '"prompt"' not in workflow
    assert '"context"' not in workflow


def test_hosted_twin_lane_is_bounded_hermetic_and_secret_free():
    workflow = (Path(__file__).parents[2] / ".github/workflows/docker.yml").read_text()
    hosted = workflow.split("  twin-contracts:\n", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    assert "runs-on: ubuntu-latest" in hosted
    assert "timeout-minutes: 5" in hosted
    assert "permissions:\n      contents: read" in hosted
    assert "HTTP_PROXY: http://127.0.0.1:9" in hosted
    assert "NO_PROXY: localhost,127.0.0.1,::1" in hosted
    assert "${{ secrets." not in hosted
    assert "actions/upload-artifact" in hosted
    assert "twin-evidence.json" in hosted
    assert "scenario" in hosted and "parity" in hosted


def test_runtime_gate_depends_on_hosted_twin_without_renaming_required_check():
    workflow = (Path(__file__).parents[2] / ".github/workflows/docker.yml").read_text()
    runtime = workflow.split("  runtime-gate:\n", 1)[1]
    assert "name: Runtime Gate" in runtime
    assert "needs: [changes, twin-contracts, integration-tests]" in runtime
    assert "Fail when required twin validation fails" in runtime


def test_calibration_is_trusted_only_and_does_not_rewrite_fixtures():
    workflow = (
        Path(__file__).parents[2] / ".github/workflows/live-calibration.yml"
    ).read_text()
    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "pull_request" not in workflow
    assert "pull_request_target" not in workflow
    assert "github.repository == 'groktopus/groktocrawl'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "persist-credentials: false" in workflow
    assert "environment: live-calibration" in workflow
    assert "git diff --exit-code -- llm-svc slopsearx-fixture provenance" in workflow
    assert "BRAVE_API_KEY" in workflow and "LLM_API_KEY" in workflow


def test_required_twin_images_are_immutable():
    root = Path(__file__).parents[2]
    compose = (root / "docker-compose.yml").read_text()
    assert (
        compose.count(
            "ghcr.io/magnus919/slopsearx@sha256:91194d146d205b1cf4688c1989da8f5f6b599a9627be23fd1ee7a4e488fda5b7"
        )
        == 2
    )
    assert "slopsearx:latest" not in compose
    for dockerfile in (
        root / "llm-svc/Dockerfile",
        root / "slopsearx-fixture/Dockerfile",
    ):
        assert (
            "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a"
            in dockerfile.read_text()
        )


def test_workflows_have_structured_trusted_and_hosted_contracts():
    root = Path(__file__).parents[2]
    live = yaml.safe_load((root / ".github/workflows/live-calibration.yml").read_text())
    docker = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())
    assert set(live["jobs"]) == {"calibrate"}
    assert live["jobs"]["calibrate"]["timeout-minutes"] == 10
    triggers = live.get("on", live.get(True))
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert "pull_request" not in triggers
    assert live["jobs"]["calibrate"]["if"] == (
        "github.repository == 'groktopus/groktocrawl' && github.ref == 'refs/heads/main'"
    )
    assert docker["jobs"]["twin-contracts"]["runs-on"] == "ubuntu-latest"
    assert docker["jobs"]["integration-tests"]["runs-on"] == [
        "self-hosted",
        "groktopus",
        "docker",
    ]
    assert "secrets." not in repr(docker["jobs"]["twin-contracts"])
    twin_run = repr(docker["jobs"]["twin-contracts"])
    assert (
        "setup-uv" in twin_run
        and "uv sync --locked --no-dev --group fast-tests" in twin_run
    )
    assert "twin-out" in twin_run and "junitxml" in twin_run


def test_published_service_images_target_amd64_and_arm64():
    root = Path(__file__).parents[2]
    docker = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())
    build_steps = docker["jobs"]["build-and-push"]["steps"]
    build_step = next(
        step
        for step in build_steps
        if step.get("uses") == "docker/build-push-action@v6"
    )

    assert build_step["with"]["platforms"] == "linux/amd64,linux/arm64"


def test_answer_evals_workflow_is_advisory_and_not_pr_gated():
    root = Path(__file__).parents[2]
    workflow = (root / ".github/workflows/answer-evals.yml").read_text()
    parsed = yaml.safe_load(workflow)
    assert "schedule" in parsed.get("on", parsed.get(True))
    assert "workflow_dispatch" in parsed.get("on", parsed.get(True))
    assert "pull_request" not in workflow
    assert "pull_request_target" not in workflow
    assert parsed["jobs"]["answer-evals"]["runs-on"] == [
        "self-hosted",
        "groktopus",
        "docker",
    ]
    assert parsed["jobs"]["answer-evals"]["if"] == (
        "github.repository == 'groktopus/groktocrawl' && github.ref == 'refs/heads/main'"
    )
    assert "timeout-minutes: 30" in workflow
    assert "--selection broad" in workflow
    assert "--http-smoke http://127.0.0.1:8084" in workflow
    assert "scripts/run_answer_evals.py" in workflow
    assert "Upload eval provenance artifact" in workflow
    assert "actions/upload-artifact" in workflow
    assert "if: always()" in workflow
    assert "Tear down eval fixture stack" in workflow
    assert "docker compose down --remove-orphans" in workflow
    # The eval is advisory — it must not be wired as a required PR check.
    assert "required" not in parsed
    docker = (root / ".github/workflows/docker.yml").read_text()
    assert "answer-evals.yml" not in docker
    runtime_gate = docker.split("name: Runtime Gate", 1)[1]
    assert "answer-evals" not in runtime_gate
    assert "needs: [changes, twin-contracts, integration-tests]" in runtime_gate


def test_compose_run_id_and_failure_provenance_are_unambiguous():
    root = Path(__file__).parents[2]
    compose = (root / "docker-compose.yml").read_text()
    workflow = (root / ".github/workflows/docker.yml").read_text()
    docker = yaml.safe_load(workflow)
    assert "${LLM_BASE_URL:-http://llm-svc:8011/v1?run_id=${" not in compose
    assert "LLM_BASE_URL=${LLM_BASE_URL:-http://llm-svc:8011/v1}" in compose
    assert "TWIN_RUN_ID=${TWIN_RUN_ID:-local}" in compose
    assert "LLM_BASE_URL=http://llm-svc:8011/v1?run_id=${TWIN_RUN_ID:-local}" in compose
    assert "LLM_BASE_URL=http://llm-svc:8011/v1?run_id=${TWIN_RUN_ID:-}" not in compose
    compose_evidence = workflow.split(
        "- name: Write Compose twin evidence manifest", 1
    )[1]
    assert (
        "TWIN_FAILURE_SOURCE: ${{ job.status == 'success' && 'none' || 'implementation' }}"
        in compose_evidence
    )
    assert "TWIN_TEST_OUTCOME: ${{ job.status }}" in compose_evidence
    assert "uv run --no-sync python scripts/twin_provenance.py" in compose_evidence
    assert (
        "TWIN_BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before }}"
        in compose_evidence
    )
    assert (
        "TWIN_BUILT_FROM_CHECKOUT: llm-svc,slopsearx-fixture,agent-svc-fixture,test-site,tier3-fixture"
        in compose_evidence
    )
    assert "tests/integration/test_twin_failure_injection.py" in compose_evidence
    assert "tests/service/test_twin_failure_injection.py" not in compose_evidence
    for host_only in (
        "tests/service/test_twin_contract.py",
        "tests/service/test_twin_network_isolation.py",
        "tests/service/test_workflow_contract.py",
    ):
        assert f"--ignore=/app/{host_only}" in workflow
        assert host_only in compose_evidence
        assert host_only in repr(docker["jobs"]["twin-contracts"])
    answer_eval = "tests/service/test_answer_evals.py"
    assert f"--ignore=/app/{answer_eval}" in workflow
    assert answer_eval in compose_evidence
    assert "scripts/run_answer_evals.py --selection narrow" in workflow
