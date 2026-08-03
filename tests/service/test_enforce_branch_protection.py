"""Unit tests for scripts/enforce-branch-protection.py (no network).

Covers the pure policy layer (payload construction, whitelist idempotency
comparator), the safety gate, the apply ordering, auth precedence, and the
JSON output — all against a recording fake transport so no network access
is required. Follows the `importlib.util.spec_from_file_location` pattern
from `test_ci_change_classification.py`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "enforce-branch-protection.py"

SPEC = (
    importlib.util.spec_from_file_location("enforce_branch_protection", SCRIPT)
    if SCRIPT.exists()
    else None
)
if SPEC and SPEC.loader:
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)
else:
    MODULE = None

META = {
    "node_id": "RULE_x",
    "source_type": "Repository",
    "source": "github",
    "created_at": "2026-08-03T00:00:00Z",
    "updated_at": "2026-08-03T00:00:00Z",
    "url": "https://api.github.com/repos/groktopus/groktocrawl/rulesets/1",
    "links": {"html": "https://github.com/groktopus/groktocrawl/rules/1"},
    "_links": {},
    "current_user_can_bypass": False,
}


class FakeTransport:
    """Recording fake for the injectable HTTP layer."""

    def __init__(
        self, handlers: dict[str, Callable[..., tuple[int, Any, dict[str, str]]]]
    ) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, str, Any]] = []

    def _dispatch(
        self, method: str, path: str, payload: Any = None
    ) -> tuple[int, Any, dict[str, str]]:
        self.calls.append((method, path, payload))
        handler = self.handlers.get(method)
        if handler is None:
            raise AssertionError(f"no handler for {method} {path}")
        return handler(path, payload)

    def get(self, path: str) -> tuple[int, Any, dict[str, str]]:
        return self._dispatch("GET", path)

    def post(self, path: str, payload: Any) -> tuple[int, Any, dict[str, str]]:
        return self._dispatch("POST", path, payload)

    def put(self, path: str, payload: Any) -> tuple[int, Any, dict[str, str]]:
        return self._dispatch("PUT", path, payload)

    def delete(self, path: str) -> tuple[int, Any, dict[str, str]]:
        return self._dispatch("DELETE", path)


def build_handlers(
    check_names: tuple[str, ...] = ("Code Quality Gate", "Runtime Gate"),
    rulesets: list[dict[str, Any]] | None = None,
    check_error: Exception | None = None,
    delete_status: int = 204,
) -> dict[str, Callable[..., tuple[int, Any, dict[str, str]]]]:
    """Handlers for a healthy scenario: check-runs, rulesets list + detail,
    echo create/update, and configurable classic-protection DELETE status."""

    def get_handler(path: str, _payload: Any) -> tuple[int, Any, dict[str, str]]:
        base = path.split("?", maxsplit=1)[0]
        if base.endswith("/check-runs"):
            if check_error is not None:
                raise check_error
            runs = [
                {"name": name, "status": "completed", "conclusion": "success"}
                for name in check_names
            ]
            return 200, {"total_count": len(runs), "check_runs": runs}, {}
        if base.endswith("/rulesets"):
            return 200, rulesets or [], {}
        match = re.match(r".*/rulesets/(\d+)$", base)
        if match:
            ruleset_id = int(match.group(1))
            for ruleset in rulesets or []:
                if ruleset.get("id") == ruleset_id:
                    return 200, ruleset, {}
            raise AssertionError(f"unknown ruleset id {ruleset_id}")
        raise AssertionError(f"unhandled GET {path}")

    def post_handler(path: str, payload: Any) -> tuple[int, Any, dict[str, str]]:
        return 201, {**payload, "id": 9001}, {}

    def put_handler(path: str, payload: Any) -> tuple[int, Any, dict[str, str]]:
        return 200, {**payload, "id": 9001}, {}

    def delete_handler(path: str, _payload: Any) -> tuple[int, Any, dict[str, str]]:
        return delete_status, {}, {}

    return {
        "GET": get_handler,
        "POST": post_handler,
        "PUT": put_handler,
        "DELETE": delete_handler,
    }


def make_api(transport: FakeTransport) -> Any:
    return MODULE.GithubApi(
        transport, owner="groktopus", repo="groktocrawl", branch="main"
    )


def fake_transport_factory(transport: FakeTransport) -> Callable[..., FakeTransport]:
    """Return an HttpTransport-compatible factory bound to a recording fake.

    The factory accepts HttpTransport's constructor signature (token and
    optional base_url/timeout) and always hands back the same fake, so
    `main()` can be exercised without network access.
    """

    def _factory(
        token: str, base_url: str = "https://api.github.com", timeout: float = 30.0
    ) -> FakeTransport:
        # token/base_url/timeout are accepted for constructor compatibility.
        return transport

    return _factory


def with_github_token(fn: Callable[[], Any]) -> Any:
    """Run fn with GITHUB_TOKEN set so auth resolution is hermetic."""
    old = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "test-token"
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old


class ModuleLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_module_imports_without_side_effects(self) -> None:
        self.assertEqual(MODULE.__name__, "enforce_branch_protection")
        # No top-level main() call: import must not parse argv or touch the network.
        self.assertTrue(callable(MODULE.main))
        self.assertTrue(callable(MODULE.ruleset_diff))
        self.assertTrue(callable(MODULE.plan_changes))
        self.assertTrue(callable(MODULE.resolve_auth))


class PolicyPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_ruleset_a_payload_matches_architecture(self) -> None:
        ruleset = MODULE.RULESET_A
        self.assertEqual(ruleset["name"], "main review policy")
        self.assertEqual(ruleset["enforcement"], "active")
        self.assertEqual(ruleset["target"], "branch")
        self.assertEqual(
            ruleset["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"]
        )
        self.assertEqual(ruleset["conditions"]["ref_name"]["exclude"], [])
        bypasses = [
            (actor["actor_type"], actor["actor_id"], actor["bypass_mode"])
            for actor in ruleset["bypass_actors"]
        ]
        # Exactly TWO bypass actors, both review-requirement exemption ONLY
        # (required checks still bind everyone via Ruleset B): dependabot[bot]
        # (app 29110, Integration) and the sole maintainer magnus919 (user
        # 942000, User) added by the 2026-08-03 maintainer self-merge
        # amendment so they can merge their own PRs without an approving
        # review. The release-please exemption was dropped (2026-08-03
        # amendment): github-actions[bot] cannot be a ruleset bypass actor
        # and the Release Please app is deprecated/not installed.
        self.assertEqual(
            bypasses,
            [
                ("Integration", 29110, "pull_request"),
                ("User", 942000, "pull_request"),
            ],
        )
        self.assertEqual(len(ruleset["rules"]), 1)
        pull_request = ruleset["rules"][0]
        self.assertEqual(pull_request["type"], "pull_request")
        self.assertEqual(
            pull_request["parameters"],
            {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "require_last_push_approval": True,
                "require_code_owner_review": False,
                "required_review_thread_resolution": True,
            },
        )

    def test_ruleset_b_payload_matches_architecture(self) -> None:
        ruleset = MODULE.RULESET_B
        self.assertEqual(ruleset["name"], "main required checks")
        self.assertEqual(ruleset["enforcement"], "active")
        self.assertEqual(ruleset["target"], "branch")
        self.assertNotIn("bypass_actors", ruleset)
        self.assertEqual(
            [rule["type"] for rule in ruleset["rules"]],
            ["required_status_checks", "non_fast_forward", "deletion"],
        )
        status_checks = ruleset["rules"][0]
        self.assertTrue(
            status_checks["parameters"]["strict_required_status_checks_policy"]
        )
        self.assertEqual(
            [
                entry["context"]
                for entry in status_checks["parameters"]["required_status_checks"]
            ],
            ["Code Quality Gate", "Runtime Gate"],
        )
        # integration_id is omitted from the payload: the REST schema types it
        # as integer (not nullable) and rejects null; absent means "any
        # source", which is the policy (VAL-ENF-003 tolerates absent-or-null).
        for entry in status_checks["parameters"]["required_status_checks"]:
            self.assertNotIn("integration_id", entry)

    def test_required_check_contexts_are_the_two_stable_gates(self) -> None:
        self.assertEqual(
            set(MODULE.REQUIRED_CHECK_CONTEXTS), {"Code Quality Gate", "Runtime Gate"}
        )


class IdempotencyComparatorTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_metadata_rich_actual_yields_no_changes(self) -> None:
        actual_a = {**MODULE.RULESET_A, **META, "id": 11}
        actual_b = {**MODULE.RULESET_B, **META, "id": 12}
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_A, actual_a), [])
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_B, actual_b), [])
        changes = MODULE.plan_changes(MODULE.RULESETS, [actual_a, actual_b])
        self.assertEqual([change["action"] for change in changes], ["none", "none"])
        self.assertTrue(all(change["differences"] == [] for change in changes))

    def test_resolved_default_branch_ref_is_normalized(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_A))
        actual["id"] = 21
        actual["conditions"] = {"ref_name": {"include": ["refs/heads/main"]}}
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_A, actual), [])

    def test_absent_exclude_is_normalized(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_B))
        actual["id"] = 22
        actual["conditions"] = {"ref_name": {"include": ["~DEFAULT_BRANCH"]}}
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_B, actual), [])

    def test_absent_integration_id_is_tolerated(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_B))
        actual["id"] = 23
        for entry in actual["rules"][0]["parameters"]["required_status_checks"]:
            entry["integration_id"] = None
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_B, actual), [])

    def test_absent_false_pull_request_parameter_is_tolerated(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_A))
        actual["id"] = 24
        del actual["rules"][0]["parameters"]["require_code_owner_review"]
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_A, actual), [])

    def test_context_order_is_ignored(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_B))
        actual["id"] = 25
        actual["rules"][0]["parameters"]["required_status_checks"].reverse()
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_B, actual), [])

    def test_bypass_actor_order_is_ignored(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_A))
        actual["id"] = 26
        actual["bypass_actors"].reverse()
        self.assertEqual(MODULE.ruleset_diff(MODULE.RULESET_A, actual), [])

    def test_policy_differences_are_detected(self) -> None:
        actual = json.loads(json.dumps(MODULE.RULESET_B))
        actual["id"] = 27
        actual["rules"][0]["parameters"]["required_status_checks"] = [
            {"context": "Code Quality Gate", "integration_id": None}
        ]
        diffs = MODULE.ruleset_diff(MODULE.RULESET_B, actual)
        self.assertTrue(any("contexts" in diff for diff in diffs))

    def test_missing_ruleset_produces_create_change(self) -> None:
        changes = MODULE.plan_changes(MODULE.RULESETS, [])
        self.assertEqual([change["action"] for change in changes], ["create", "create"])
        self.assertEqual(changes[0]["name"], "main review policy")
        self.assertEqual(changes[1]["name"], "main required checks")

    def test_partially_missing_ruleset_produces_one_create(self) -> None:
        actual_a = json.loads(json.dumps(MODULE.RULESET_A))
        actual_a["id"] = 31
        changes = MODULE.plan_changes(MODULE.RULESETS, [actual_a])
        self.assertEqual(changes[0]["action"], "none")
        self.assertEqual(changes[1]["action"], "create")


class SafetyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_missing_context_aborts_apply_with_zero_mutations(self) -> None:
        transport = FakeTransport(
            build_handlers(check_names=("Runtime Gate",), rulesets=[])
        )
        api = make_api(transport)
        with self.assertRaises(MODULE.ApplyAbortError) as ctx:
            MODULE.run_orchestrator(
                api,
                mode="apply",
                auth_source="gh",
                owner="groktopus",
                repo="groktocrawl",
                branch="main",
            )
        self.assertIn("aborting apply", str(ctx.exception))
        methods = [call[0] for call in transport.calls]
        self.assertEqual([m for m in methods if m in ("POST", "PUT", "DELETE")], [])
        self.assertTrue(all(m == "GET" for m in methods))

    def test_dry_run_with_unreachable_safety_gate_does_not_abort(self) -> None:
        transport = FakeTransport(
            build_handlers(check_error=MODULE.TransportError("boom"), rulesets=[])
        )
        api = make_api(transport)
        summary = MODULE.run_orchestrator(
            api,
            mode="dry-run",
            auth_source="gh",
            owner="groktopus",
            repo="groktocrawl",
            branch="main",
        )
        self.assertFalse(summary["safety_gate"]["verified"])
        self.assertIn("boom", summary["safety_gate"]["error"])
        report = f"{MODULE.policy_report_text()}\n\n{MODULE.render_text(summary)}"
        self.assertIn("=== POLICY REPORT ===", report)
        self.assertIn("=== SAFETY GATE ===", report)
        methods = [call[0] for call in transport.calls]
        self.assertEqual([m for m in methods if m in ("POST", "PUT", "DELETE")], [])

    def test_main_dry_run_unreachable_safety_gate_exits_zero(self) -> None:
        transport = FakeTransport(
            build_handlers(check_error=MODULE.TransportError("boom"), rulesets=[])
        )
        original = MODULE.HttpTransport
        MODULE.HttpTransport = fake_transport_factory(transport)

        def run() -> int:
            return MODULE.main(
                [
                    "--dry-run",
                    "--owner",
                    "groktopus",
                    "--repo",
                    "groktocrawl",
                    "--branch",
                    "main",
                ]
            )

        try:
            rc = with_github_token(run)
        finally:
            MODULE.HttpTransport = original
        self.assertEqual(rc, 0)

    def test_main_apply_missing_context_returns_nonzero(self) -> None:
        transport = FakeTransport(
            build_handlers(check_names=("Runtime Gate",), rulesets=[])
        )
        original = MODULE.HttpTransport
        MODULE.HttpTransport = fake_transport_factory(transport)

        def run() -> int:
            return MODULE.main(
                [
                    "--apply",
                    "--owner",
                    "groktopus",
                    "--repo",
                    "groktocrawl",
                    "--branch",
                    "main",
                ]
            )

        try:
            rc = with_github_token(run)
        finally:
            MODULE.HttpTransport = original
        self.assertEqual(rc, 1)
        methods = [call[0] for call in transport.calls]
        self.assertEqual([m for m in methods if m in ("POST", "PUT", "DELETE")], [])


class ApplySequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_apply_orders_rulesets_then_classic_removal(self) -> None:
        transport = FakeTransport(
            build_handlers(
                check_names=("Code Quality Gate", "Runtime Gate"),
                rulesets=[],
                delete_status=204,
            )
        )
        api = make_api(transport)
        summary = MODULE.run_orchestrator(
            api,
            mode="apply",
            auth_source="gh",
            owner="groktopus",
            repo="groktocrawl",
            branch="main",
        )
        calls = transport.calls
        posts = [
            (index, call[1], call[2])
            for index, call in enumerate(calls)
            if call[0] == "POST"
        ]
        deletes = [
            (index, call[1]) for index, call in enumerate(calls) if call[0] == "DELETE"
        ]
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(deletes), 1)
        self.assertLess(posts[0][0], posts[1][0])
        self.assertLess(posts[1][0], deletes[0][0])
        self.assertIn("/rulesets", posts[0][1])
        self.assertIn("main review policy", json.dumps(posts[0][2]))
        self.assertIn("main required checks", json.dumps(posts[1][2]))
        self.assertIn("/branches/main/protection", deletes[0][1])
        self.assertEqual(
            summary["applied"],
            [
                {"name": "main review policy", "action": "create"},
                {"name": "main required checks", "action": "create"},
            ],
        )
        self.assertTrue(summary["classic_protection_removed"]["deleted"])

    def test_apply_stops_on_verification_failure_before_classic_removal(self) -> None:
        handlers = build_handlers(
            check_names=("Code Quality Gate", "Runtime Gate"),
            rulesets=[],
            delete_status=204,
        )

        def failing_post(path: str, payload: Any) -> tuple[int, Any, dict[str, str]]:
            return 201, {**payload, "id": 9001, "enforcement": "disabled"}, {}

        handlers["POST"] = failing_post
        transport = FakeTransport(handlers)
        api = make_api(transport)
        with self.assertRaises(MODULE.ApplyAbortError):
            MODULE.run_orchestrator(
                api,
                mode="apply",
                auth_source="gh",
                owner="groktopus",
                repo="groktocrawl",
                branch="main",
            )
        methods = [call[0] for call in transport.calls]
        self.assertNotIn("DELETE", methods)
        # Only Ruleset A was attempted before the abort.
        self.assertEqual(methods.count("POST"), 1)

    def test_apply_treats_404_on_classic_protection_as_already_removed(self) -> None:
        transport = FakeTransport(
            build_handlers(
                check_names=("Code Quality Gate", "Runtime Gate"),
                rulesets=[],
                delete_status=404,
            )
        )
        api = make_api(transport)
        summary = MODULE.run_orchestrator(
            api,
            mode="apply",
            auth_source="gh",
            owner="groktopus",
            repo="groktocrawl",
            branch="main",
        )
        self.assertTrue(summary["classic_protection_removed"]["already_removed"])
        self.assertFalse(summary["classic_protection_removed"]["deleted"])


class NoMutationWithoutApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_dry_run_issues_zero_mutating_calls(self) -> None:
        transport = FakeTransport(
            build_handlers(
                check_names=("Code Quality Gate", "Runtime Gate"), rulesets=[]
            )
        )
        api = make_api(transport)
        summary = MODULE.run_orchestrator(
            api,
            mode="dry-run",
            auth_source="gh",
            owner="groktopus",
            repo="groktocrawl",
            branch="main",
        )
        methods = [call[0] for call in transport.calls]
        self.assertEqual([m for m in methods if m in ("POST", "PUT", "DELETE")], [])
        self.assertEqual(
            [change["action"] for change in summary["changes"]], ["create", "create"]
        )

    def test_apply_is_the_only_mode_that_reaches_mutation_code(self) -> None:
        self.assertTrue(hasattr(MODULE, "apply_plan"))
        # The orchestrator only calls apply_plan when mode == "apply".
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if mode == "apply":', source)


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_github_token_takes_precedence_over_gh(self) -> None:
        def boom() -> str:
            raise AssertionError(
                "gh runner must not be called when GITHUB_TOKEN is set"
            )

        token, source = MODULE.resolve_auth(
            env={"GITHUB_TOKEN": "env-token"}, gh_token_runner=boom
        )
        self.assertEqual((token, source), ("env-token", "GITHUB_TOKEN"))

    def test_gh_fallback_used_without_github_token(self) -> None:
        token, source = MODULE.resolve_auth(env={}, gh_token_runner=lambda: "gh-token")
        self.assertEqual((token, source), ("gh-token", "gh"))

    def test_neither_auth_source_raises_clear_error(self) -> None:
        with self.assertRaises(MODULE.AuthError) as ctx:
            MODULE.resolve_auth(env={}, gh_token_runner=lambda: None)
        self.assertIn("GITHUB_TOKEN", str(ctx.exception))

    def test_main_neither_auth_returns_error_without_mutations(self) -> None:
        transport = FakeTransport({})
        original_transport = MODULE.HttpTransport
        original_runner = MODULE._default_gh_token
        MODULE.HttpTransport = fake_transport_factory(transport)
        MODULE._default_gh_token = lambda: None
        old = os.environ.pop("GITHUB_TOKEN", None)

        def run() -> int:
            return MODULE.main(
                ["--dry-run", "--owner", "groktopus", "--repo", "groktocrawl"]
            )

        try:
            rc = run()
        finally:
            MODULE.HttpTransport = original_transport
            MODULE._default_gh_token = original_runner
            if old is not None:
                os.environ["GITHUB_TOKEN"] = old
        self.assertEqual(rc, 2)
        self.assertEqual(transport.calls, [])


class JsonOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        if MODULE is None:
            self.skipTest(
                "scripts/enforce-branch-protection.py not present in this environment"
            )

    def test_summary_json_parses_with_required_keys(self) -> None:
        transport = FakeTransport(
            build_handlers(
                check_names=("Code Quality Gate", "Runtime Gate"), rulesets=[]
            )
        )
        api = make_api(transport)
        summary = MODULE.run_orchestrator(
            api,
            mode="dry-run",
            auth_source="GITHUB_TOKEN",
            owner="groktopus",
            repo="groktocrawl",
            branch="main",
        )
        text = json.dumps(summary, indent=2, sort_keys=True)
        parsed = json.loads(text)
        self.assertIn("main review policy", parsed["rulesets"])
        self.assertIn("main required checks", parsed["rulesets"])
        self.assertIn("no_changes", parsed)
        self.assertIn("safety_gate", parsed)
        self.assertEqual(parsed["auth_source"], "GITHUB_TOKEN")
        self.assertEqual(parsed["mode"], "dry-run")

    def test_main_dry_run_json_emits_single_valid_document(self) -> None:
        transport = FakeTransport(
            build_handlers(
                check_names=("Code Quality Gate", "Runtime Gate"), rulesets=[]
            )
        )
        original = MODULE.HttpTransport
        MODULE.HttpTransport = fake_transport_factory(transport)
        buf = io.StringIO()

        def run() -> int:
            with contextlib.redirect_stdout(buf):
                return MODULE.main(
                    [
                        "--dry-run",
                        "--json",
                        "--owner",
                        "groktopus",
                        "--repo",
                        "groktocrawl",
                        "--branch",
                        "main",
                    ]
                )

        try:
            rc = with_github_token(run)
        finally:
            MODULE.HttpTransport = original
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertIn("main review policy", parsed["rulesets"])
        self.assertIn("main required checks", parsed["rulesets"])
        self.assertIn("no_changes", parsed)
        self.assertIn("safety_gate", parsed)


if __name__ == "__main__":
    unittest.main()
