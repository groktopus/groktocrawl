"""Contract checks for sanitized LLM fixture CI evidence."""

import os
import re
import subprocess
import sys
import textwrap
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
            "ghcr.io/magnus919/slopsearx@sha256:0f89531014aa9692a555a8549d63f5e38a1f3d6fc56b6512f1af394574ba9c30"
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


def test_droid_action_sanitizer_removes_debug_uploads_and_fails_closed(tmp_path):
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/droid-review.yml").read_text())
    job = workflow["jobs"]["droid-review"]
    checkout = next(
        step
        for step in job["steps"]
        if step.get("name") == "Checkout pinned Droid action"
    )
    assert checkout["with"]["path"] == ".github/actions/droid-action"
    action_ref = checkout["with"]["ref"]
    action_step = next(
        step for step in job["steps"] if step.get("name") == "Run Droid Auto Review"
    )
    assert action_step["env"]["NOUS_API_KEY"] == "${{ secrets.NOUS_API_KEY }}"
    assert "NOUS_API_KEY" not in job.get("env", {})
    cleanup = next(
        step for step in job["steps"] if step.get("name") == "Remove Droid runner state"
    )
    assert cleanup["if"] == "always()"
    assert ".github/actions/droid-action" not in cleanup["run"]
    sanitizer = next(
        step
        for step in job["steps"]
        if step.get("name") == "Disable credential-bearing Droid debug artifacts"
    )
    match = re.search(r"python3 - .*<<'PY'\n(.*?)\nPY\n", sanitizer["run"], re.DOTALL)
    assert match is not None
    sanitizer_code = textwrap.dedent(match.group(1))

    action_dir = tmp_path / "droid-action"
    action_dir.mkdir()
    action_file = action_dir / "action.yml"
    original = """\
        name: Droid
        runs:
          using: composite
          steps:
            - name: Post review
              run: publish
            - name: Update comment with job link
              run: update
            - name: Collect .factory debug files
              run: collect
            - name: Upload debug artifacts
              uses: actions/upload-artifact@v4
              with:
                retention-days: 7
        """
    original = textwrap.dedent(original)
    action_file.write_text(original)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nprintf '%s\\n' \"$DROID_ACTION_TEST_REV\"\n")
    fake_git.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    env["DROID_ACTION_TEST_REV"] = "wrong-revision"
    mismatch = subprocess.run(
        [sys.executable, "-c", sanitizer_code, str(action_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mismatch.returncode != 0
    assert action_file.read_text() == original

    env["DROID_ACTION_TEST_REV"] = action_ref
    changed_source = original.replace("Upload debug artifacts", "Upload diagnostics")
    action_file.write_text(changed_source)
    drift = subprocess.run(
        [sys.executable, "-c", sanitizer_code, str(action_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drift.returncode != 0
    assert action_file.read_text() == changed_source

    env["DROID_ACTION_TEST_REV"] = action_ref
    action_file.write_text(original)
    sanitized = subprocess.run(
        [sys.executable, "-c", sanitizer_code, str(action_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sanitized.returncode == 0, sanitized.stderr
    result = action_file.read_text()
    assert "name: Post review" in result
    assert "name: Update comment with job link" in result
    assert "Collect .factory debug files" not in result
    assert "Upload debug artifacts" not in result
    assert "actions/upload-artifact" not in result
