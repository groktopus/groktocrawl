"""Contract tests for the conservative CI runtime classifier."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

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


class RuntimeGateWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.build_and_push = workflow.split("\n  build-and-push:\n", maxsplit=1)[
            1
        ].split("\n  integration-tests:\n", maxsplit=1)[0]
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
    ) -> bool:
        """Evaluate the workflow's two supported integration-test lanes."""
        return (
            event_name == "pull_request"
            and changes_result == "success"
            and requires_full_runtime == "true"
        ) or (event_name == "push" and build_result == "success")

    def test_docker_build_matrix_is_push_only(self) -> None:
        self.assertIn("if: github.event_name == 'push'", self.build_and_push)
        self.assertIn("matrix:\n        include:", self.build_and_push)
        self.assertIn("- service: agent-svc", self.build_and_push)
        self.assertIn("- service: scraper-svc", self.build_and_push)

    def test_docs_only_pull_request_cannot_run_integration_tests(self) -> None:
        self.assertEqual(
            self.integration_condition,
            "always() && ((github.event_name == 'pull_request' && "
            "needs.changes.result == 'success' && "
            "needs.changes.outputs.requires_full_runtime == 'true') || "
            "(github.event_name == 'push' && needs.build-and-push.result == 'success'))",
        )
        self.assertFalse(
            self.integration_tests_runs_for(
                event_name="pull_request",
                changes_result="success",
                requires_full_runtime="false",
                build_result="skipped",
            )
        )

    def test_full_runtime_pull_request_can_run_integration_tests(self) -> None:
        self.assertTrue(
            self.integration_tests_runs_for(
                event_name="pull_request",
                changes_result="success",
                requires_full_runtime="true",
                build_result="skipped",
            )
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

    def test_integration_test_staging_copies_only_required_contract_inputs(
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
        self.assertNotIn(
            'docker cp .github/workflows/. "$svc":/app/.github/workflows/',
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
        self.assertEqual(self.runtime_gate.count("exit 1"), 2)


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


if __name__ == "__main__":
    unittest.main()
