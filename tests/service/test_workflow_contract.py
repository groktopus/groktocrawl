"""Contract checks for sanitized LLM fixture CI evidence."""

import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
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
    assert (
        "needs: [changes, twin-contracts, integration-tests, research-runtime]"
        in runtime
    )
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


def test_mainline_ci_routes_the_real_agent_and_direct_search_clients_to_fixture():
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())
    steps = {
        step.get("name"): step.get("run", "")
        for step in workflow["jobs"]["integration-tests"]["steps"]
    }
    setup = steps["Set up .env"]
    assert 'echo "SEARXNG_URL=http://slopsearx-fixture:8080" >> .env' in setup
    integration = steps["Run integration tests"]
    assert "SEARCH_BASE_URL=http://slopsearx-fixture:8080" in integration
    assert "SEARCH_BASE_URL=http://slopsearx:8080" not in integration
    probe = steps["Verify LLM fixture contract and agent routing"]
    assert (
        "assert os.environ['SEARXNG_URL'] == 'http://slopsearx-fixture:8080'" in probe
    )
    assert "http://agent-svc:8080/v2/search" in probe
    assert "result['url'].startswith('http://test-site:8000/')" in probe
    assert "payload['success'] and results" in probe
    # These inputs configure test clients and the agent without changing either
    # SlopSearX service's production contract.
    assert (
        "docker compose --profile indexing --profile durable-research up -d"
        in steps["Start the Docker stack"]
    )


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


@pytest.mark.parametrize("filename", ["docker.yml", "runtime.yml"])
def test_runtime_container_tests_use_hash_locked_fast_test_requirements(filename):
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
    runtime = workflow["jobs"]["integration-tests"]
    steps = {step.get("name"): step.get("run", "") for step in runtime["steps"]}
    export_command = (
        "uv export --locked --only-group fast-tests --no-dev --no-emit-project "
        "--no-emit-workspace --no-header --format requirements.txt "
        "--output-file runtime-test-requirements.txt"
    )
    for name in ("Install critical test dependencies", "Run integration tests"):
        command = steps[name]
        assert export_command in command
        assert (
            'docker cp runtime-test-requirements.txt "$svc:/tmp/runtime-test-requirements.txt"'
            in command
        )
        assert (
            "pip install --require-hashes -r /tmp/runtime-test-requirements.txt"
            in command
        )
        assert "pip install pytest " not in command

    groups = tomllib.loads((root / "pyproject.toml").read_text())["dependency-groups"]
    fast_tests = groups["fast-tests"]
    assert any(item.startswith("curl_cffi") for item in fast_tests)
    assert any(item.startswith("pytest-timeout>=2") for item in fast_tests)
    lock = (root / "uv.lock").read_text()
    assert 'name = "pytest-timeout"' in lock


@pytest.mark.parametrize("filename", ["docker.yml", "runtime.yml"])
def test_container_test_staging_includes_service_contract_inputs(filename):
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
    steps = workflow["jobs"]["integration-tests"]["steps"]
    staging = next(
        step["run"]
        for step in steps
        if step.get("name") == "Copy test files and source directories"
    )
    for fixture in (
        ".env.sample",
        "groktocrawl",
        "docker-compose.ci.yml",
        "compose.experimental-candidate.yml",
        "compose.protected-flare-ci.yml",
        "compose.protected-capture-ci.yml",
        "compose.protected-capture-lab.yml",
    ):
        assert f'docker cp {fixture} "$svc":/app/{fixture}' in staging
    assert 'docker cp .github/workflows/. "$svc":/app/.github/workflows/' in staging
    assert (
        "for script in ci_fork_pr_guard.py enforce-branch-protection.py reconcile-jobs.py; do"
        in staging
    )
    assert 'docker cp "scripts/$script" "$svc:/app/scripts/$script"' in staging
    assert 'docker cp agent-svc/agent/. "$svc":/app/agent/' in staging
    assert "docker cp pyproject.toml" not in staging


def test_docker_runtime_uses_isolated_agent_images_and_restarts_after_source_copy():
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())
    steps = workflow["jobs"]["integration-tests"]["steps"]
    names = [step.get("name") for step in steps]
    isolate = next(
        step["run"]
        for step in steps
        if step.get("name") == "Isolate agent CI image tags"
    )
    verify = next(
        step["run"]
        for step in steps
        if step.get("name") == "Verify running agent image identity"
    )
    restart = next(
        step["run"]
        for step in steps
        if step.get("name") == "Restart agent after source refresh"
    )

    assert "groktocrawl-ci/agent:${run_tag}" in isolate
    assert "groktocrawl-ci/agent-fixture:${run_tag}" in isolate
    assert "docker image tag ghcr.io/groktopus/groktocrawl-agent" in isolate
    assert "GITHUB_EVENT_NAME" in isolate and "GITHUB_REF" in isolate
    assert "AFFECTED_SERVICES" in isolate
    assert "slopsearx" not in isolate.lower()
    assert "mcp-svc:\n    ports: !reset []" in isolate
    assert "docker inspect --format '{{.Image}}'" in verify
    assert "docker image inspect --format '{{.Id}}'" in verify
    assert "docker compose restart agent-svc" in restart
    assert "localhost:{os.environ['AGENT_PORT']}/health" in restart
    assert names.index("Pull freshly published service images") < names.index(
        "Isolate agent CI image tags"
    )
    assert names.index("Isolate agent CI image tags") < names.index(
        "Build service images (service-local PR build / tag-push full build)"
    )
    assert (
        names.index("Start the Docker stack")
        < names.index("Verify running agent image identity")
        < names.index("Start test fixtures")
    )
    assert (
        names.index("Copy test files and source directories")
        < names.index("Restart agent after source refresh")
        < names.index("Install critical test dependencies")
    )
    teardown = next(
        step["run"] for step in steps if step.get("name") == "Tear down stack"
    )
    assert 'docker image rm "groktocrawl-ci/agent:${run_tag}"' in teardown
    assert '"groktocrawl-ci/agent-fixture:${run_tag}"' in teardown


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
    assert (
        "needs: [changes, twin-contracts, integration-tests, research-runtime]"
        in runtime_gate
    )


def test_compose_run_id_and_failure_provenance_are_unambiguous():
    root = Path(__file__).parents[2]
    compose = (root / "docker-compose.yml").read_text()
    workflow = (root / ".github/workflows/docker.yml").read_text()
    docker = yaml.safe_load(workflow)
    runtime_workflow = (root / ".github/workflows/runtime.yml").read_text()
    for source in (workflow, runtime_workflow):
        assert source.count("TWIN_EVENT_KIND: ${{ github.event_name }}") == 2
        assert (
            source.count(
                "TWIN_EVENT_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}"
            )
            == 2
        )
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
    assert "TWIN_EVENT_KIND: ${{ github.event_name }}" in compose_evidence
    assert (
        "TWIN_EVENT_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}"
        in compose_evidence
    )
    assert "TWIN_BUILT_FROM_CHECKOUT: >-" in compose_evidence
    assert "github.ref != 'refs/heads/main'" in compose_evidence
    assert "needs.changes.outputs.affected_services == 'all'" in compose_evidence
    assert "' agent-svc '" in compose_evidence
    assert "test-site,tier3-fixture,agent-svc' ||" in compose_evidence
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


def test_research_runtime_is_required_by_the_mainline_gate():
    root = Path(__file__).parents[2]
    docker = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())
    runtime = yaml.safe_load((root / ".github/workflows/runtime.yml").read_text())
    assert (
        docker["jobs"]["research-runtime"]["uses"] == "./.github/workflows/runtime.yml"
    )
    assert "research-runtime" in docker["jobs"]["runtime-gate"]["needs"]
    assert runtime["jobs"]["runtime-gate"]["name"] == "Research Runtime Gate"
    assert set(runtime["jobs"]["runtime-gate"]["needs"]) >= {
        "research-storage",
        "protected-browser-profile",
        "protected-flare-profile",
        "protected-capture-composition",
    }


def test_published_agent_health_revision_is_baked_into_image():
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())
    build = next(
        step
        for step in workflow["jobs"]["build-and-push"]["steps"]
        if step.get("uses") == "docker/build-push-action@v6"
    )
    assert "GROKTOCRAWL_REVISION=${{ github.sha }}" in build["with"]["build-args"]
    dockerfile = (root / "agent-svc/Dockerfile").read_text()
    assert "ENV GROKTOCRAWL_REVISION=${GROKTOCRAWL_REVISION}" in dockerfile
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert not any(
        value.startswith("GROKTOCRAWL_REVISION=")
        for value in compose["services"]["agent-svc"]["environment"]
    )


def test_called_runtime_artifacts_do_not_collide_with_parent_workflow():
    root = Path(__file__).parents[2] / ".github/workflows"
    artifact_sets = []
    for filename in ("docker.yml", "runtime.yml"):
        workflow = yaml.safe_load((root / filename).read_text())
        names = [
            step["with"]["name"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if "upload-artifact" in step.get("uses", "")
        ]
        assert len(names) == len(set(names))
        artifact_sets.append(set(names))
    assert not artifact_sets[0] & artifact_sets[1]


def test_image_publication_preserves_untagged_multiarch_child_manifests():
    workflow = yaml.safe_load(
        (Path(__file__).parents[2] / ".github/workflows/docker.yml").read_text()
    )
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            # GHCR exposes platform and attestation manifests as untagged versions.
            # A generic untagged-version cleanup destroys live index dependencies.
            assert "prune untagged" not in step.get("name", "").lower()
            action = step.get("uses", "").lower()
            assert "delete-package-versions" not in action
            assert "container-retention-policy" not in action
            command = step.get("run", "")
            if "/packages/container/" in command:
                assert not re.search(r"(?:--method|-X)\s+DELETE\b", command)


def test_full_integration_steps_allow_report_headroom_without_relaxing_test_timeouts():
    root = Path(__file__).parents[2]
    for filename in ("docker.yml", "runtime.yml"):
        workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
        job = workflow["jobs"]["integration-tests"]
        step = next(s for s in job["steps"] if s.get("name") == "Run integration tests")
        assert step["timeout-minutes"] == 25
        assert "--timeout=120" in step["run"]
        assert 'docker cp "$svc:/app/coverage/." coverage/' in step["run"]
        assert 'exit "$pytest_status"' in step["run"]
        if filename == "runtime.yml":
            assert job["timeout-minutes"] == 60
