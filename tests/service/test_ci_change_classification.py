"""Contract tests for the conservative CI runtime classifier."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = ROOT / "scripts" / "classify_ci_changes.py"
WORKFLOW = ROOT / ".github" / "workflows" / "docker.yml"
FAST_TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "fast-tests.yml"
SPEC = importlib.util.spec_from_file_location("classify_ci_changes", CLASSIFIER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CiChangeClassificationTests(unittest.TestCase):
    def test_docs_only_changes_do_not_require_runtime(self) -> None:
        self.assertFalse(MODULE.requires_full_runtime(["docs/guides/ci.md"]))

    def test_repository_prose_only_changes_do_not_require_runtime(self) -> None:
        self.assertFalse(
            MODULE.requires_full_runtime(
                [
                    "README.md",
                    "AGENTS.md",
                    "CONTRIBUTING.md",
                    ".github/ISSUE_TEMPLATE/bug.yml",
                    "docs/adr/README.md",
                ]
            )
        )

    def test_docs_plus_source_requires_runtime(self) -> None:
        self.assertTrue(
            MODULE.requires_full_runtime(
                ["docs/guides/ci.md", "agent-svc/agent/app.py"]
            )
        )

    def test_runtime_and_unrecognized_paths_require_runtime(self) -> None:
        for path in (
            ".github/workflows/docker.yml",
            "docker-compose.yml",
            "tests/service/test_health.py",
            "scripts/check-docs-surface.py",
            "requirements.txt",
            "notes.txt",
        ):
            with self.subTest(path=path):
                self.assertTrue(MODULE.requires_full_runtime([path]))

    def test_empty_path_set_requires_runtime(self) -> None:
        self.assertTrue(MODULE.requires_full_runtime([]))
        self.assertTrue(MODULE.requires_full_runtime([""]))
        self.assertTrue(MODULE.requires_full_runtime(["  "]))

    def test_affected_services_maps_only_changed_runtime_services(self) -> None:
        self.assertEqual(
            MODULE.affected_services(["agent-svc/agent/app.py"]),
            frozenset({"agent-svc"}),
        )
        self.assertEqual(
            MODULE.affected_services(
                ["scraper-svc/scraper/fetch.py", "parse-svc/parse_svc/app.py"]
            ),
            frozenset({"scraper-svc", "parse-svc"}),
        )

    def test_affected_services_escalates_cross_cutting_paths_to_all(self) -> None:
        for path in ("docker-compose.yml", "common/models.py"):
            with self.subTest(path=path):
                self.assertEqual(MODULE.affected_services([path]), frozenset({"all"}))

    def test_affected_services_escalates_unrecognized_runtime_paths_to_all(
        self,
    ) -> None:
        for path in (
            "notes.txt",
            "requirements.txt",
            "scripts/check-docs-surface.py",
            ".github/workflows/docker.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(MODULE.affected_services([path]), frozenset({"all"}))

    def test_affected_services_escalates_empty_and_malformed_input_to_all(self) -> None:
        self.assertEqual(MODULE.affected_services([]), frozenset({"all"}))
        self.assertEqual(MODULE.affected_services([""]), frozenset({"all"}))
        self.assertEqual(MODULE.affected_services(["  "]), frozenset({"all"}))

    def test_affected_services_docs_only_paths_are_ignored(self) -> None:
        self.assertEqual(MODULE.affected_services(["docs/guides/ci.md"]), frozenset())

    def test_root_level_markdown_files_are_docs_only(self) -> None:
        # #561: a root-level *.md (e.g. ROADMAP.md, CHANGELOG.md, SECURITY.md) is
        # documentation, not runtime code, so it must not require full runtime
        # validation nor trigger any image rebuild.
        for path in ("ROADMAP.md", "CHANGELOG.md", "SECURITY.md", "VISION.md"):
            with self.subTest(path=path):
                self.assertFalse(MODULE.requires_full_runtime([path]))
                self.assertEqual(MODULE.affected_services([path]), frozenset())

    def test_github_markdown_files_are_docs_only(self) -> None:
        # #561: prose markdown under .github/ (e.g. PULL_REQUEST_TEMPLATE.md) is
        # documentation too, mirroring the existing .github/ISSUE_TEMPLATE/ prefix.
        # Non-markdown workflow/config files under .github/ still escalate.
        self.assertFalse(
            MODULE.requires_full_runtime([".github/PULL_REQUEST_TEMPLATE.md"])
        )
        self.assertEqual(
            MODULE.affected_services([".github/PULL_REQUEST_TEMPLATE.md"]),
            frozenset(),
        )
        self.assertTrue(MODULE.requires_full_runtime([".github/workflows/docker.yml"]))
        self.assertEqual(
            MODULE.affected_services([".github/workflows/docker.yml"]),
            frozenset({"all"}),
        )

    def test_root_level_markdown_is_ignored_in_mixed_set(self) -> None:
        self.assertEqual(
            MODULE.affected_services(["ROADMAP.md", "agent-svc/agent/app.py"]),
            frozenset({"agent-svc"}),
        )
        self.assertTrue(
            MODULE.requires_full_runtime(["ROADMAP.md", "agent-svc/agent/app.py"])
        )

    def test_root_level_non_markdown_still_escalates(self) -> None:
        # The root-*.md extension must not swallow unknown root-level files:
        # only markdown becomes docs-only; anything else escalates to all.
        for path in ("requirements.txt", "notes.txt", "Makefile"):
            with self.subTest(path=path):
                self.assertTrue(MODULE.requires_full_runtime([path]))
                self.assertEqual(MODULE.affected_services([path]), frozenset({"all"}))

    def test_affected_services_ignores_docs_paths_in_mixed_set(self) -> None:
        self.assertEqual(
            MODULE.affected_services(["docs/guides/ci.md", "agent-svc/agent/app.py"]),
            frozenset({"agent-svc"}),
        )

    def test_cli_reads_stdin_and_prints_boolean(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLASSIFIER)],
            input="README.md\ndocs/guides/ci.md\n",
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertEqual(result.stdout, "false\n")
        self.assertEqual(result.stderr, "")

    def test_cli_accepts_positional_paths(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CLASSIFIER),
                "docs/guides/ci.md",
                "agent-svc/agent/app.py",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertEqual(result.stdout, "true\n")

    def test_cli_classifies_root_markdown_as_docs_only(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLASSIFIER)],
            input="ROADMAP.md\n",
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertEqual(result.stdout, "false\n")

    def test_twin_selection_table(self) -> None:
        cases = [
            (["llm-svc/llm_svc/app.py"], True, "llm"),
            (["slopsearx-fixture/slopsearx_fixture/app.py"], True, "search"),
            (["agent-svc/agent/searxng_client.py"], True, "search"),
            (["docs/ci.md"], False, "none"),
            (["docs/ci.md", "docker-compose.yml"], True, "all"),
            (["unknown/path"], True, "all"),
            ([], True, "all"),
            ([""], True, "all"),
        ]
        for paths, expected_bool, expected_selection in cases:
            with self.subTest(paths=paths):
                self.assertEqual(MODULE.requires_twin_contracts(paths), expected_bool)
                self.assertEqual(MODULE.twin_test_selection(paths), expected_selection)

    def test_answer_evals_paths_require_runtime_and_twin_contracts(self) -> None:
        # Issue #570: eval harness and fixture scenario changes are runtime +
        # twin-relevant (never docs-only), so the narrow pre-merge eval step runs.
        for path in (
            "evals/answer_evals/harness.py",
            "evals/answer_evals/grading.py",
            "evals/answer_evals/routing.py",
            "evals/answer_evals/manifest.json",
            "evals/answer_evals/cases/positive-001-answer-grounded.json",
            "scripts/run_answer_evals.py",
            "llm-svc/llm_svc/app.py",
            "slopsearx-fixture/slopsearx_fixture/app.py",
            "test-site/test_site/app.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(MODULE.requires_full_runtime([path]))
                self.assertTrue(MODULE.requires_twin_contracts([path]))
                if path.startswith(("evals/", "scripts/", "test-site/")):
                    expected_selection = "all"
                elif path.startswith("llm-svc/"):
                    expected_selection = "llm"
                else:
                    expected_selection = "search"
                self.assertEqual(MODULE.twin_test_selection([path]), expected_selection)

    def test_changed_path_shell_block_is_valid_and_keeps_zero_sha_else(self) -> None:
        workflow = WORKFLOW.read_text()
        block = workflow.split('if [ "${{ github.event_name }}"', 1)[1]
        block = (
            'if [ "${{ github.event_name }}"'
            + block.split("\n\n  twin-contracts:", 1)[0]
        )
        block = textwrap.dedent(block).replace("${{", "${PLACEHOLDER_")
        block = block.replace("}}", "}")
        result = subprocess.run(
            ["bash", "-n"], input=block, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git diff-tree --root", block)
        self.assertIn(
            'if [ "$base" = "0000000000000000000000000000000000000000" ]; then', block
        )
        self.assertIn(
            'else\n              changed_paths=$(git diff --name-only "$base" "$head")',
            block,
        )

    def test_embedded_llm_probe_python_compiles_and_run_id_is_propagated(self) -> None:
        workflow = WORKFLOW.read_text()
        parsed = yaml.safe_load(workflow)
        integration = parsed["jobs"]["integration-tests"]["steps"]
        run = next(
            step["run"]
            for step in integration
            if step.get("name") == "Verify LLM fixture contract and agent routing"
        )
        probes = re.findall(r'python3 -c "\n(.*?)\n"', run, flags=re.DOTALL)
        self.assertEqual(len(probes), 2)
        for index, probe in enumerate(probes):
            compile(probe, f"docker.yml LLM probe {index}", "exec")
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("TWIN_RUN_ID=${TWIN_RUN_ID:-local}", compose)
        self.assertIn("run_id=${TWIN_RUN_ID:-local}", compose)
        self.assertNotIn(
            "LLM_BASE_URL=http://llm-svc:8011/v1?run_id=${TWIN_RUN_ID:-}", compose
        )
        self.assertIn("run_id=${{ github.run_id }}-${{ github.run_attempt }}", workflow)


class RuntimeGateWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.build_and_push = workflow.split("\n  build-and-push:\n", maxsplit=1)[
            1
        ].split("\n  integration-tests:\n", maxsplit=1)[0]
        self.build_matrix_services = {
            line.split("service:", 1)[1].strip()
            for line in self.build_and_push.splitlines()
            if line.strip().startswith("- service:")
        }
        self.integration_tests = workflow.split("\n  integration-tests:\n", maxsplit=1)[
            1
        ].split("\n  runtime-gate:\n", maxsplit=1)[0]
        self.integration_condition = " ".join(
            self.integration_tests.split("if: >-\n", maxsplit=1)[1]
            .split("\n    runs-on:", maxsplit=1)[0]
            .split()
        )
        self.changes = workflow.split("\n  changes:\n", maxsplit=1)[1].split(
            "\n  build-and-push:\n", maxsplit=1
        )[0]
        self.pull_request_classification = self.changes.split(
            'if [ "${{ github.event_name }}" = "pull_request" ]; then\n',
            maxsplit=1,
        )[1].split("\n          else\n", maxsplit=1)[0]
        self.runtime_gate = workflow.split("\n  runtime-gate:\n", maxsplit=1)[1]

    def integration_tests_runs_for(
        self,
        *,
        event_name: str,
        changes_result: str,
        requires_full_runtime: str,
        build_result: str,
        fork: bool,
    ) -> bool:
        """Evaluate the workflow's integration-test lanes (PR, fork-PR, push)."""
        return (
            event_name == "pull_request"
            and changes_result == "success"
            and requires_full_runtime == "true"
            and not fork
        ) or (event_name == "push" and build_result == "success")

    def test_docker_build_matrix_is_push_only(self) -> None:
        self.assertIn("if: github.event_name == 'push'", self.build_and_push)
        self.assertIn("matrix:\n        include:", self.build_and_push)
        self.assertIn("- service: agent-svc", self.build_and_push)
        self.assertIn("- service: scraper-svc", self.build_and_push)

    def test_runtime_services_match_build_and_push_matrix(self) -> None:
        # Base service changes use the service-local lane; protected-image
        # changes require the full required research-runtime validation.
        self.assertTrue(self.build_matrix_services)
        self.assertEqual(
            set(MODULE.RUNTIME_SERVICES) | set(MODULE.PROTECTED_IMAGE_SERVICES),
            self.build_matrix_services,
        )
        for service in MODULE.PROTECTED_IMAGE_SERVICES:
            self.assertEqual(
                MODULE.affected_services([f"{service}/Dockerfile"]), {"all"}
            )
            self.assertTrue(MODULE.requires_full_runtime([f"{service}/Dockerfile"]))

    def test_docs_only_pull_request_cannot_run_integration_tests(self) -> None:
        # The fork admission term uses a STRING comparison on format('{0}', ...)
        # rather than `fork == false`: loose equality coerces Null->0 and
        # false->0, so `null == false` was TRUE and admitted deleted-fork PRs
        # (head.repo.fork == null) onto the self-hosted lane. Rendering to
        # 'true'/'false'/'' makes admission require a proven same-repository PR.
        self.assertEqual(
            self.integration_condition,
            "always() && ((github.event_name == 'pull_request' && "
            "needs.changes.result == 'success' && "
            "needs.changes.outputs.requires_full_runtime == 'true' && "
            "format('{0}', github.event.pull_request.head.repo.fork) == 'false') || "
            "(github.event_name == 'push' && needs.build-and-push.result == 'success'))",
        )
        self.assertFalse(
            self.integration_tests_runs_for(
                event_name="pull_request",
                changes_result="success",
                requires_full_runtime="false",
                build_result="skipped",
                fork=False,
            )
        )

    def test_full_runtime_pull_request_can_run_integration_tests(self) -> None:
        self.assertTrue(
            self.integration_tests_runs_for(
                event_name="pull_request",
                changes_result="success",
                requires_full_runtime="true",
                build_result="skipped",
                fork=False,
            )
        )

    def test_fork_pull_request_cannot_run_integration_tests(self) -> None:
        # String-rendered comparison (see test_docs_only_pull_request...):
        # `fork == false` loose-equals null, so the raw comparison admitted
        # deleted forks; only a literal 'false' rendering may run on the lane.
        self.assertIn(
            "format('{0}', github.event.pull_request.head.repo.fork) == 'false'",
            self.integration_condition,
        )
        self.assertFalse(
            self.integration_tests_runs_for(
                event_name="pull_request",
                changes_result="success",
                requires_full_runtime="true",
                build_result="skipped",
                fork=True,
            )
        )
        # Null fork (deleted fork / missing head.repo) must also be excluded:
        # fail-closed off the self-hosted lane (#562 scrutiny round 1).
        parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        integration_if = parsed["jobs"]["integration-tests"]["if"]
        assert isinstance(integration_if, str)
        normalized = " ".join(integration_if.split())
        self.assertIn(
            "format('{0}', github.event.pull_request.head.repo.fork) == 'false'",
            normalized,
        )

    def test_pull_request_classification_diffs_merge_base_to_head(self) -> None:
        self.assertIn(
            'merge_base=$(git merge-base "$base" "$head")',
            self.pull_request_classification,
        )
        self.assertIn(
            'changed_paths=$(git diff --name-only "$merge_base" "$head")',
            self.pull_request_classification,
        )
        self.assertNotIn(
            'changed_paths=$(git diff --name-only "$base" "$head")',
            self.pull_request_classification,
        )

    def test_changes_job_exposes_affected_services_output(self) -> None:
        self.assertIn(
            "affected_services: ${{ steps.classify.outputs.affected_services }}",
            self.changes,
        )
        self.assertIn(
            "--affected-services",
            self.changes,
        )
        self.assertIn(
            'echo "affected_services=$affected_services" >> "$GITHUB_OUTPUT"',
            self.changes,
        )

    def test_pr_build_step_is_service_local_and_gated_on_affected_services(
        self,
    ) -> None:
        self.assertIn(
            "Build service images (service-local PR build / tag-push full build)",
            self.integration_tests,
        )
        self.assertIn(
            "needs.changes.outputs.affected_services",
            self.integration_tests,
        )
        self.assertIn(
            "docker compose --profile indexing build $affected",
            self.integration_tests,
        )
        self.assertIn(
            "docker compose --profile indexing build",
            self.integration_tests,
        )
        self.assertIn("github.event_name == 'pull_request'", self.integration_tests)

    def test_pr_lane_pulls_published_images_before_service_local_build(self) -> None:
        # Reproducibility (#494): before the service-local PR build, the stack is
        # refreshed from the latest published images so unchanged services match a
        # known commit; GHCR login runs for pull_request events.
        self.assertIn("Pull freshly published service images", self.integration_tests)
        self.assertIn("docker compose --profile indexing pull", self.integration_tests)
        self.assertIn(
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
            self.integration_tests,
        )
        self.assertIn("github.event_name == 'pull_request'", self.integration_tests)

    def test_main_push_pulls_published_images_without_rebuilding(self) -> None:
        self.assertIn("Pull freshly published service images", self.integration_tests)
        self.assertIn("docker compose --profile indexing pull", self.integration_tests)
        self.assertIn(
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
            self.integration_tests,
        )

    def test_tag_push_builds_full_stack_from_tagged_source(self) -> None:
        # P1 (#494 review): tag pushes (v*) must not pull stale :latest; they
        # rebuild the full runtime stack from the tagged source.
        self.assertIn(
            "github.event_name == 'push' && github.ref != 'refs/heads/main'",
            self.integration_tests,
        )
        self.assertIn(
            "Building the full runtime stack.",
            self.integration_tests,
        )

    def test_start_stack_step_no_longer_uses_build(self) -> None:
        self.assertIn("docker compose --profile indexing up -d", self.integration_tests)
        self.assertNotIn(
            "docker compose --profile indexing up --build -d", self.integration_tests
        )
        # The only `up` is build-less: no event rebuilds the full stack on top of
        # an already-provisioned image set.
        self.assertNotIn("up --build", self.integration_tests)

    def test_integration_test_staging_copies_required_contract_inputs(
        self,
    ) -> None:
        self.assertIn(
            "docker compose exec -T agent-svc mkdir -p /app/scripts",
            self.integration_tests,
        )
        self.assertIn(
            'docker cp scripts/classify_ci_changes.py "$svc":/app/scripts/classify_ci_changes.py',
            self.integration_tests,
        )
        self.assertNotIn(
            'docker cp scripts/. "$svc":/app/scripts/', self.integration_tests
        )
        self.assertIn(
            "docker compose exec -T agent-svc mkdir -p /app/.github/workflows",
            self.integration_tests,
        )
        self.assertIn(
            'docker cp .github/workflows/docker.yml "$svc":/app/.github/workflows/docker.yml',
            self.integration_tests,
        )
        self.assertIn(
            'docker cp .github/workflows/fast-tests.yml "$svc":/app/.github/workflows/fast-tests.yml',
            self.integration_tests,
        )
        self.assertIn(
            'docker cp agent-svc/agent/. "$svc":/app/agent/',
            self.integration_tests,
        )
        self.assertIn(
            "for pkg in scraper-svc/scraper parse-svc/parse_svc portal-svc/portal browser-svc/browser_svc llm-svc/llm_svc slopsearx-fixture/slopsearx_fixture common; do",
            self.integration_tests,
        )
        self.assertIn(
            "SEARCH_BASE_URL=http://slopsearx:8080",
            self.integration_tests,
        )
        self.assertIn(
            "AGENT_BASE_URL=http://agent-svc-fixture:8080",
            self.integration_tests,
        )
        self.assertIn("Wait for slopsearx-fixture", self.integration_tests)
        self.assertIn("Timed out waiting for slopsearx-fixture", self.integration_tests)
        self.assertIn("Wait for agent-svc-fixture", self.integration_tests)
        self.assertIn("Timed out waiting for agent-svc-fixture", self.integration_tests)
        self.assertIn(
            "docker compose --profile fixture build test-site tier3-fixture slopsearx-fixture agent-svc-fixture",
            self.integration_tests,
        )
        self.assertIn(
            "docker compose --profile fixture up -d --force-recreate llm-svc tier3-fixture test-site slopsearx-fixture agent-svc-fixture",
            self.integration_tests,
        )
        self.assertIn(
            "Verify LLM fixture contract and agent routing", self.integration_tests
        )
        self.assertIn("agent LLM routing verified", self.integration_tests)
        self.assertIn(
            "endpoint = 'http://llm-svc:8011/v1/chat/completions'",
            self.integration_tests,
        )
        self.assertIn(
            "LLM fixture citation and schema contracts verified",
            self.integration_tests,
        )
        self.assertIn(
            "Run targeted source-backed agent contracts", self.integration_tests
        )
        self.assertIn("Reset isolated test state", self.integration_tests)
        self.assertIn("valkey-cli -n 0 FLUSHDB", self.integration_tests)
        self.assertIn("valkey-cli -n 1 FLUSHDB", self.integration_tests)
        reset_index = self.integration_tests.index("Reset isolated test state")
        probe_index = self.integration_tests.index(
            "Verify LLM fixture contract and agent routing"
        )
        targeted_index = self.integration_tests.index(
            "Run targeted source-backed agent contracts"
        )
        critical_index = self.integration_tests.index("Critical journey smoke")
        self.assertLess(reset_index, probe_index)
        self.assertLess(probe_index, targeted_index)
        self.assertLess(targeted_index, critical_index)
        self.assertNotIn(
            "SEARXNG_URL=http://slopsearx-fixture:8080 docker compose --profile indexing up -d",
            self.integration_tests,
        )
        self.assertIn("--cov=/app/agent", self.integration_tests)
        self.assertNotIn("--cov=/app/agent-svc/agent", self.integration_tests)
        self.assertIn(
            'docker cp .github/workflows/. "$svc":/app/.github/workflows/',
            self.integration_tests,
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
            self.assertIn(
                f'docker cp {fixture} "$svc":/app/{fixture}', self.integration_tests
            )
        self.assertIn(
            "for script in ci_fork_pr_guard.py enforce-branch-protection.py reconcile-jobs.py; do",
            self.integration_tests,
        )
        self.assertIn("-m 'not external'", self.integration_tests)

    def test_changed_line_gate_skips_ref_creation_without_base_sha(self) -> None:
        zero_sha = "0" * 40
        guard = f'if [ "$COVERAGE_BASE_SHA" = "{zero_sha}" ]; then'
        self.assertIn(guard, self.integration_tests)
        self.assertIn("no prior commit exists for this ref", self.integration_tests)

    def test_changed_line_gate_uses_the_checked_out_head_sha(self) -> None:
        self.assertIn("COVERAGE_HEAD_SHA: ${{ github.sha }}", self.integration_tests)
        self.assertNotIn(
            "COVERAGE_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.integration_tests,
        )

    def test_integration_coverage_report_is_not_appended_to_critical_report(
        self,
    ) -> None:
        self.assertNotIn("--cov-append", self.integration_tests)

    def test_coverage_summary_distinguishes_missing_json_from_missing_gate_summary(
        self,
    ) -> None:
        self.assertIn(
            "id: coverage_gate",
            self.integration_tests,
        )
        self.assertIn(
            "steps.coverage_gate.outcome",
            self.integration_tests,
        )
        self.assertIn(
            "Changed-line coverage comparison did not run because an earlier test step failed.",
            self.integration_tests,
        )
        self.assertNotIn("comparison may have been skipped", self.integration_tests)

    def test_narrow_answer_evals_step_is_bounded_twin_gated_and_uploads_provenance(
        self,
    ) -> None:
        # Issue #570: the pre-merge narrow eval step runs in-process against the
        # twins plus one real-route HTTP /v2/answer smoke over agent-svc-fixture.
        self.assertIn(
            "Run narrow grounded-answer eval (in-process + HTTP smoke)",
            self.integration_tests,
        )
        self.assertIn("--selection narrow", self.integration_tests)
        # The smoke URL hits agent-svc-fixture's PUBLISHED host port from the
        # runner; the integration lane's host ports are allocated dynamically at
        # run time (see test_integration_lane_allocates_host_ports_dynamically),
        # so the flag must interpolate the exported variable rather than pin a
        # literal port that any co-tenant could be squatting.
        self.assertIn(
            '--http-smoke "http://127.0.0.1:${AGENT_FIXTURE_HOST_PORT}"',
            self.integration_tests,
        )
        self.assertNotIn("--http-smoke http://127.0.0.1:18084", self.integration_tests)
        self.assertIn("timeout-minutes: 5", self.integration_tests)
        eval_block = self.integration_tests.split(
            "Run narrow grounded-answer eval (in-process + HTTP smoke)", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("requires_twin_contracts == 'true'", eval_block)
        self.assertIn("requires_full_runtime == 'true'", eval_block)
        self.assertIn("scripts/run_answer_evals.py", eval_block)
        self.assertIn("Upload answer-evals provenance", self.integration_tests)
        self.assertIn("answer-evals-provenance", self.integration_tests)
        self.assertIn("path: eval-out/", self.integration_tests)
        # The eval step runs after the fixtures are up (agent-svc-fixture smoke).
        self.assertLess(
            self.integration_tests.index("Wait for agent-svc-fixture"),
            self.integration_tests.index(
                "Run narrow grounded-answer eval (in-process + HTTP smoke)"
            ),
        )

    def test_integration_lane_allocates_host_ports_dynamically(self) -> None:
        # Shared self-hosted daemon deconfliction, round 2: a fixed 18xxx range
        # still loses the race when a co-tenant squats exactly one port of the
        # block (observed on 18081 while its neighbors stayed free), so the lane
        # must allocate a free /100 block at run time via real bind probes and
        # export it through $GITHUB_ENV (which overrides the job-level pins for
        # every subsequent step). Host-side URLs interpolate the exported names;
        # only the job-level fallback block may contain 180xx literals.
        parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        integration = parsed["jobs"]["integration-tests"]
        steps = {step.get("name"): step for step in integration["steps"]}
        self.assertIn("Allocate deconflicted host ports", steps)
        allocator = steps["Allocate deconflicted host ports"]["run"]
        start_index = self.integration_tests.index("Start the Docker stack")
        env_block = self.integration_tests[:start_index]
        self.assertLess(
            self.integration_tests.index("Allocate deconflicted host ports"),
            start_index,
        )
        # The allocator is stdlib-only python and bind-probes before committing;
        # strip the shell heredoc wrapper before syntax-checking the payload.
        allocator_body = allocator.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        compile(allocator_body, "docker.yml port allocator", "exec")
        for token in ("socket.socket", 'bind(("0.0.0.0", port))', "GITHUB_ENV"):
            self.assertIn(token, allocator_body)
        # The logical slot map must stay aligned with the compose ${VAR:-default}
        # interpolation set: one /100 block covers every published service port.
        slots = {
            "AGENT_PORT": 80,
            "SCRAPER_HOST_PORT": 1,
            "SEMANTIC_HOST_PORT": 3,
            "TEST_SITE_HOST_PORT": 5,
            "TIER3_HOST_PORT": 6,
            "LLM_HOST_PORT": 11,
            "SLOPSEARX_HOST_PORT": 81,
            "PORTAL_HOST_PORT": 82,
            "SLOPSEARX_FIXTURE_HOST_PORT": 83,
            "AGENT_FIXTURE_HOST_PORT": 84,
        }
        for name, offset in slots.items():
            self.assertIn(f'("{name}", {offset})', allocator_body)
        # The full candidate sweep: /100 blocks from 18000 through 19900.
        self.assertIn("range(18000, 19901, 100)", allocator_body)
        # Fail-closed: exhausting candidates aborts instead of half-binding.
        self.assertIn(
            "No free /100 host-port block found in 18000-19900", allocator_body
        )
        # Every exported name is kept as a documented job-level fallback pin.
        for name in slots:
            self.assertRegex(env_block, rf'(?m)^\s+{name}: "\d{{5}}"$')

    def test_host_side_urls_interpolate_allocated_ports(self) -> None:
        # Host-side callers must read the dynamically allocated ports. Embedded
        # python payloads are delivered through quoted-delimiter heredocs
        # (<<'PY'), which pass the source to the interpreter verbatim, so they
        # read the exported names from os.environ; only genuine shell contexts
        # expand "$VAR"/"${VAR}" directly. In-container service-DNS URLs stay
        # literal by design.
        host_literals = []
        for match in re.finditer(
            r"(?:localhost|127\.0\.0\.1):(\d{4,5})", self.integration_tests
        ):
            host_literals.append(match.group(0))
        self.assertEqual(
            host_literals,
            [],
            f"host-side URL with hardcoded port leaked into the integration job: {host_literals}",
        )
        wait_urls = {
            "Wait for services to be healthy": ("AGENT_PORT", "/health"),
            "Wait for semantic-svc": ("SEMANTIC_HOST_PORT", "/health"),
            "Wait for tier3-fixture": ("TIER3_HOST_PORT", "/health"),
            "Wait for test-site": ("TEST_SITE_HOST_PORT", "/health"),
            "Wait for slopsearx-fixture": ("SLOPSEARX_FIXTURE_HOST_PORT", "/health"),
            "Wait for agent-svc-fixture": ("AGENT_FIXTURE_HOST_PORT", "/health"),
        }
        steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
            "integration-tests"
        ]["steps"]
        for name, (var, path) in wait_urls.items():
            run = next(step["run"] for step in steps if step.get("name") == name)
            # Heredoc convention: quoted delimiter + os.environ-built URL.
            self.assertIn("python3 - <<'PY'", run, f"{name} must use a <<'PY' heredoc")
            self.assertIn(
                f"url = f\"http://localhost:{{os.environ['{var}']}}{path}\"",
                run,
                f"{name} must build its URL from os.environ['{var}']",
            )
        reset_run = next(
            step["run"]
            for step in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
                "integration-tests"
            ]["steps"]
            if step.get("name") == "Reset isolated test state"
        )
        self.assertIn(
            "base_url = f\"http://localhost:{os.environ['LLM_HOST_PORT']}/diagnostics/reset\"",
            reset_run,
        )
        self.assertIn(
            "base_url = f\"http://localhost:{os.environ['SLOPSEARX_FIXTURE_HOST_PORT']}/ledger/reset\"",
            reset_run,
        )
        ledger_heredoc = next(
            step["run"]
            for step in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
                "integration-tests"
            ]["steps"]
            if step.get("name") == "Capture sanitized search fixture ledger"
        )
        self.assertIn("os.environ['SLOPSEARX_FIXTURE_HOST_PORT']", ledger_heredoc)
        diagnostics_heredoc = next(
            step["run"]
            for step in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
                "integration-tests"
            ]["steps"]
            if step.get("name") == "Capture sanitized LLM fixture diagnostics"
        )
        self.assertIn("os.environ['LLM_HOST_PORT']", diagnostics_heredoc)

    def test_no_shell_interpolation_inside_python_payloads(self) -> None:
        # Negative regression guard for the CI failure introduced by d3a4a09:
        # inside a python3 -c "<double-quoted>" payload, bash expands $VAR
        # BEFORE python parses the source, so shell quote-concatenation like
        # urlopen('http://localhost:'"$AGENT_PORT"'/health') reaches python as
        # 'http://localhost:'18080'/health' — an illegal implicit string
        # concatenation across two string literals and a number — and dies with
        # "SyntaxError: invalid syntax". The convention enforced here: python
        # code is delivered via quoted-delimiter heredocs (verbatim, no shell
        # expansion) and reads dynamic values from os.environ; shell variable
        # expansion may appear only outside python payloads.
        steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
            "integration-tests"
        ]["steps"]
        quote_concat_offenders = []
        noncompiling_heredocs = []
        for step in steps:
            run = step.get("run")
            if not isinstance(run, str):
                continue
            name = step.get("name", "<unnamed>")
            # Shell/python quote-concatenation seams ('...'"$VAR"'...' and the
            # brace form '...'"${VAR}"'...') are never valid in any context in
            # this workflow: python receives garbage, and plain shell uses plain
            # "$VAR".
            if "'\"$" in run or "\"'$" in run:
                quote_concat_offenders.append(name)
            # Quoted heredocs deliver python verbatim, so their raw bodies must
            # compile AND must contain no shell expansions at all.
            lines = run.split("\n")
            index = 0
            while index < len(lines):
                if "<<'" in lines[index]:
                    delimiter = lines[index].split("<<'", 1)[1].split("'", 1)[0]
                    body_lines = []
                    index += 1
                    while index < len(lines) and lines[index].rstrip() != delimiter:
                        body_lines.append(lines[index])
                        index += 1
                    body = "\n".join(body_lines) + "\n"
                    try:
                        compile(body, name, "exec")
                    except SyntaxError as exc:
                        noncompiling_heredocs.append(f"{name}: {exc}")
                    self.assertNotIn(
                        "$",
                        body.replace("$$", ""),
                        f"{name}: quoted heredoc must not rely on shell expansion "
                        "(read values from os.environ instead)",
                    )
                index += 1
        self.assertEqual(
            quote_concat_offenders,
            [],
            f"shell/python quote-concatenation leaked into steps: {quote_concat_offenders}",
        )
        self.assertEqual(
            noncompiling_heredocs,
            [],
            f"heredoc python payloads failed to compile: {noncompiling_heredocs}",
        )

    def test_failure_diagnostics_capture_port_collisions(self) -> None:
        # Future co-tenant collisions must be immediately attributable: the
        # failure report records listening sockets plus every container's port
        # map, right after the compose state listing.
        report = self.integration_tests.split("Collect diagnostics on failure", 1)[
            1
        ].split("\n      - name:", 1)[0]
        ps_index = report.index("=== docker compose ps -a ===")
        sockets_index = report.index("=== listening TCP sockets ===")
        ports_index = report.index("=== all containers with ports ===")
        stats_index = report.index("=== docker stats --no-stream ===")
        self.assertLess(ps_index, sockets_index)
        self.assertLess(sockets_index, ports_index)
        self.assertLess(ports_index, stats_index)
        self.assertIn("ss -ltnp || netstat -tlnp || true", report)
        self.assertIn("docker ps --format '{{.Names}}\\t{{.Ports}}' || true", report)

    def test_runtime_gate_only_bypasses_docs_only_pull_requests(self) -> None:
        self.assertIn("if: always()", self.runtime_gate)
        self.assertIn(
            "Runtime integration was intentionally not needed for this docs-only pull request.",
            self.runtime_gate,
        )
        self.assertIn(
            "github.event_name == 'pull_request' && needs.changes.result == 'success' &&",
            self.runtime_gate,
        )
        self.assertIn(
            "needs.changes.outputs.requires_full_runtime == 'false'", self.runtime_gate
        )

    def test_fork_pr_runtime_gate_succeeds_noop(self) -> None:
        self.assertIn("if: always()", self.runtime_gate)
        self.assertIn(
            "Fork pull request: self-hosted integration skipped for security.",
            self.runtime_gate,
        )
        # Pin the fork exclusion to the fail-when-runtime-failed step
        # specifically, so removing it regresses this test. The exclusion uses
        # the string-rendered fork comparison: `fork == true` is FALSE for a
        # deleted-fork PR (null), which would leave that PR failing Runtime
        # Gate with only a generic message; rendering ('true'/''/'false')
        # treats every untrusted fork state as excluded from required runtime.
        self.assertIn(
            "format('{0}', github.event.pull_request.head.repo.fork) != 'false') &&",
            self.runtime_gate,
        )
        # The summarize step must cover deleted forks (null) too — runbook
        # point 3 promises an explicit skipped-for-security summary for forks.
        summarize_fork = self.runtime_gate.split(
            "- name: Summarize fork pull request (integration skipped)", 1
        )[1].split("- name:", 1)[0]
        self.assertIn(
            "format('{0}', github.event.pull_request.head.repo.fork) != 'false'",
            summarize_fork,
        )
        self.assertNotIn("head.repo.fork == true", summarize_fork)

    def test_runtime_gate_fails_when_classification_or_required_runtime_fails(
        self,
    ) -> None:
        self.assertIn(
            "- name: Fail when change classification fails", self.runtime_gate
        )
        self.assertIn(
            "github.event_name == 'pull_request' &&\n          needs.changes.result != 'success'",
            self.runtime_gate,
        )
        self.assertIn(
            "- name: Fail when required runtime validation fails", self.runtime_gate
        )
        self.assertIn("needs.integration-tests.result != 'success'", self.runtime_gate)
        self.assertIn("needs.research-runtime.result != 'success'", self.runtime_gate)
        self.assertEqual(self.runtime_gate.count("exit 1"), 4)


class FastTestsWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = FAST_TESTS_WORKFLOW.read_text(encoding="utf-8")

    def test_fast_tests_are_named_and_run_for_main_pushes_and_pull_requests(
        self,
    ) -> None:
        self.assertIn("name: Fast Tests", self.workflow)
        self.assertIn("push:\n    branches: [main]", self.workflow)
        self.assertIn("pull_request:\n    branches: [main]", self.workflow)
        self.assertIn('python-version: "3.12"', self.workflow)

    def test_fast_tests_install_locked_declared_dependencies(self) -> None:
        self.assertIn("uv sync --locked --no-dev --group fast-tests", self.workflow)
        self.assertNotIn("--all-packages", self.workflow)

    def test_fast_tests_target_only_unit_and_service_suites_without_docker(
        self,
    ) -> None:
        self.assertIn("pytest tests/unit/ tests/service/", self.workflow)
        self.assertIn("pytest-cov", self.workflow)
        self.assertIn("--cov-report=json:coverage/fast.json", self.workflow)
        self.assertNotIn("--no-cov", self.workflow)
        self.assertNotIn("tests/integration", self.workflow)
        self.assertNotIn("docker", self.workflow.lower())

    def test_fast_tests_pythonpath_includes_answer_eval_fixture_package(self) -> None:
        self.assertIn(
            "agent-svc:scraper-svc:llm-svc:slopsearx-fixture:parse-svc",
            self.workflow,
        )

    def test_changed_line_gate_skips_ref_creation_without_base_sha(self) -> None:
        zero_sha = "0" * 40
        guard = f'if [ "$COVERAGE_BASE_SHA" = "{zero_sha}" ]; then'
        self.assertIn(guard, self.workflow)
        self.assertIn("no prior commit exists for this ref", self.workflow)

    def test_changed_line_gate_uses_the_checked_out_head_sha(self) -> None:
        self.assertIn("COVERAGE_HEAD_SHA: ${{ github.sha }}", self.workflow)
        self.assertNotIn(
            "COVERAGE_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.workflow,
        )

    def test_coverage_summary_distinguishes_missing_json_from_missing_gate_summary(
        self,
    ) -> None:
        self.assertIn(
            "id: coverage_gate",
            self.workflow,
        )
        self.assertIn(
            "steps.coverage_gate.outcome",
            self.workflow,
        )
        self.assertIn(
            "Changed-line coverage comparison did not run because an earlier test step failed.",
            self.workflow,
        )
        self.assertNotIn("comparison may have been skipped", self.workflow)


if __name__ == "__main__":
    unittest.main()
