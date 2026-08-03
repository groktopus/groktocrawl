#!/usr/bin/env python3
"""Declarative, idempotent enforcer for the main-branch QA policy.

Implements the user-approved policy from `docs/adr/0046-*` and
`library/policy.md` for the repository's default branch as two GitHub
repository rulesets:

  * Ruleset A "main review policy" - one approving review for humans with
    stale-review dismissal, last-push re-approval, and required review
    thread resolution; dependabot[bot] (app 29110) and the sole maintainer
    magnus919 (user 942000) are exempt from the review rule only (bypass_mode
    "pull_request"). The maintainer self-merge exemption was added
    (2026-08-03) so magnus919 can merge their own PRs without an approving
    review. The release-please exemption was dropped (2026-08-03):
    github-actions[bot] (the actual author of release-please PRs) cannot be
    a ruleset bypass actor and the Release Please app (40688) is deprecated,
    so release-please PRs require a human approving review.
  * Ruleset B "main required checks" - required_status_checks (strict,
    contexts exactly "Code Quality Gate" + "Runtime Gate"), non_fast_forward
    and deletion; NO bypass actors, so bots and admins must both pass the
    required checks.

Why two rulesets: `bypass_actors` exempts an actor from the ENTIRE ruleset,
so a single combined ruleset would let the bots merge with failing checks.

Behavior:

  * `--dry-run` (the default) never mutates anything: it prints the policy
    report, verifies the safety gate (both required check contexts exist in
    live check runs on the target branch head) and computes the change plan.
    A missing or unreachable safety gate is reported but never aborts.
  * `--apply` is the ONLY mode that issues mutating API calls. It aborts
    with zero mutations if the safety gate fails, then applies Ruleset A,
    Ruleset B, verifies both are active with the exact expected rules, and
    only then removes the legacy classic branch protection (404 treated as
    already removed).

The pure policy layer (payloads + whitelist idempotency comparator) is
separated from the HTTP layer so the module is unit-testable without
network. Authentication: `GITHUB_TOKEN` preferred, `gh auth token` fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

API_BASE = "https://api.github.com"
DEFAULT_BRANCH = "main"
REQUIRED_CHECK_CONTEXTS = ("Code Quality Gate", "Runtime Gate")

# --- Policy payloads (architecture.md section 3.1 / library/policy.md) ---

RULESET_A: dict[str, Any] = {
    "name": "main review policy",
    "enforcement": "active",
    "target": "branch",
    "conditions": {
        "ref_name": {
            "include": ["~DEFAULT_BRANCH"],
            "exclude": [],
        }
    },
    "bypass_actors": [
        {
            "actor_type": "Integration",
            "actor_id": 29110,
            "bypass_mode": "pull_request",
        },
        {
            "actor_type": "User",
            "actor_id": 942000,
            "bypass_mode": "pull_request",
        },
    ],
    "rules": [
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "require_last_push_approval": True,
                "require_code_owner_review": False,
                "required_review_thread_resolution": True,
            },
        }
    ],
}

RULESET_B: dict[str, Any] = {
    "name": "main required checks",
    "enforcement": "active",
    "target": "branch",
    "conditions": {
        "ref_name": {
            "include": ["~DEFAULT_BRANCH"],
            "exclude": [],
        }
    },
    "rules": [
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                # integration_id is intentionally omitted: GitHub's REST schema
                # types it as integer (not nullable) and rejects `null`, while
                # an absent value means "any source" — identical policy (the
                # idempotency comparator treats absent and null as equal).
                "required_status_checks": [
                    {"context": "Code Quality Gate"},
                    {"context": "Runtime Gate"},
                ],
            },
        },
        {"type": "non_fast_forward"},
        {"type": "deletion"},
    ],
}

RULESETS: list[dict[str, Any]] = [RULESET_A, RULESET_B]

# API metadata fields that the whitelist comparator ignores (VAL-TOOL-008).
METADATA_FIELDS = frozenset(
    {
        "created_at",
        "updated_at",
        "source_type",
        "source",
        "node_id",
        "url",
        "links",
        "_links",
        "current_user_can_bypass",
    }
)

# The pull_request parameters that define the review policy. Parameters whose
# desired value is False are tolerated when absent from the live payload.
PULL_REQUEST_POLICY: dict[str, Any] = {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews_on_push": True,
    "require_last_push_approval": True,
    "require_code_owner_review": False,
    "required_review_thread_resolution": True,
}


class EnforceError(Exception):
    """Base error for the enforcer."""


class AuthError(EnforceError):
    """No GitHub authentication available."""


class TransportError(EnforceError):
    """The HTTP layer could not complete a request."""


class ApplyAbortError(EnforceError):
    """A pre-mutation or verification failure aborted --apply."""


# --- Authentication ---------------------------------------------------------


def _default_gh_token() -> str | None:
    """Return the gh auth token only when gh is genuinely authenticated.

    `gh auth token` alone can return a system-keychain token even when no gh
    host is configured (e.g. when GH_CONFIG_DIR is isolated), so `gh auth
    status` must confirm an actual gh login before trusting the token.
    """
    try:
        status = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return None
    if status.returncode != 0:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            check=True,
            text=True,
            timeout=15,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return None
    return result.stdout.strip() or None


def resolve_auth(
    env: Mapping[str, str] | None = None,
    gh_token_runner: Callable[[], str | None] | None = None,
) -> tuple[str, str]:
    """Return (token, auth_source). GITHUB_TOKEN is preferred over gh.

    `auth_source` is one of "GITHUB_TOKEN" or "gh". Raises AuthError when
    neither source provides a token.
    """
    env = os.environ if env is None else env
    token = (env.get("GITHUB_TOKEN") or "").strip()
    if token:
        return token, "GITHUB_TOKEN"
    token = (gh_token_runner or _default_gh_token)()
    if token:
        return token, "gh"
    raise AuthError(
        "no GitHub authentication available: set GITHUB_TOKEN or run `gh auth login`"
    )


# --- HTTP layer (injectable) ------------------------------------------------


class HttpTransport:
    """Minimal JSON HTTP layer over the Python standard library.

    Injectable so unit tests can substitute a recording fake that simulates
    200/404/missing-context without network access.
    """

    def __init__(
        self,
        token: str,
        base_url: str = API_BASE,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, Any, dict[str, str]]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "enforce-branch-protection/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                return response.status, self._parse(body), dict(response.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, self._parse(body), dict(exc.headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"{method} {url} failed: {exc}") from exc

    @staticmethod
    def _parse(body: bytes) -> Any:
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def get(self, path: str) -> tuple[int, Any, dict[str, str]]:
        return self._request("GET", path)

    def post(
        self, path: str, payload: dict[str, Any]
    ) -> tuple[int, Any, dict[str, str]]:
        return self._request("POST", path, payload)

    def put(
        self, path: str, payload: dict[str, Any]
    ) -> tuple[int, Any, dict[str, str]]:
        return self._request("PUT", path, payload)

    def delete(self, path: str) -> tuple[int, Any, dict[str, str]]:
        return self._request("DELETE", path)


def _next_link(headers: Mapping[str, str]) -> str | None:
    link = headers.get("Link", "")
    for part in link.split(","):
        match = re.match(r"<([^>]+)>;\s*rel=\"next\"", part.strip())
        if match:
            return match.group(1)
    return None


class GithubApi:
    """Read/write operations against the GitHub REST API."""

    def __init__(
        self,
        transport: HttpTransport,
        owner: str,
        repo: str,
        branch: str = DEFAULT_BRANCH,
    ) -> None:
        self.transport = transport
        self.owner = owner
        self.repo = repo
        self.branch = branch

    def _rulesets_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}/rulesets"

    def _ruleset_path(self, ruleset_id: int) -> str:
        return f"/repos/{self.owner}/{self.repo}/rulesets/{ruleset_id}"

    def _check_runs_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}/commits/{self.branch}/check-runs"

    def _protection_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}/branches/{self.branch}/protection"

    # --- reads ---

    def fetch_all_check_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        url = f"{self._check_runs_path()}?per_page=100"
        while url:
            status, data, headers = self.transport.get(url)
            if status != 200:
                raise TransportError(f"GET check-runs returned HTTP {status}: {data}")
            runs.extend(data.get("check_runs") or [])
            url = _next_link(headers)
        return runs

    def safety_gate(self) -> tuple[bool, list[str], str | None]:
        """Verify both required check contexts exist in live check runs on the
        target branch head. Returns (verified, missing_contexts, error).

        Never raises: a network failure is reported as unverified so dry-run
        can proceed (and apply can abort) with a clear message.
        """
        try:
            runs = self.fetch_all_check_runs()
        except TransportError as exc:
            return False, [], f"could not fetch check-runs: {exc}"
        names = {run.get("name") for run in runs if run.get("name")}
        missing = [ctx for ctx in REQUIRED_CHECK_CONTEXTS if ctx not in names]
        return (len(missing) == 0), missing, None

    def list_rulesets(self) -> list[dict[str, Any]]:
        status, data, _ = self.transport.get(self._rulesets_path())
        if status != 200:
            raise TransportError(f"GET rulesets returned HTTP {status}: {data}")
        return data or []

    def get_ruleset(self, ruleset_id: int) -> dict[str, Any]:
        status, data, _ = self.transport.get(self._ruleset_path(ruleset_id))
        if status != 200:
            raise TransportError(
                f"GET ruleset {ruleset_id} returned HTTP {status}: {data}"
            )
        return data

    # --- writes (only reachable from --apply) ---

    def create_ruleset(self, payload: dict[str, Any]) -> dict[str, Any]:
        status, data, _ = self.transport.post(self._rulesets_path(), payload)
        if status not in (200, 201):
            raise TransportError(f"POST rulesets returned HTTP {status}: {data}")
        return data

    def update_ruleset(
        self, ruleset_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        status, data, _ = self.transport.put(self._ruleset_path(ruleset_id), payload)
        if status != 200:
            raise TransportError(
                f"PUT ruleset {ruleset_id} returned HTTP {status}: {data}"
            )
        return data

    def delete_branch_protection(self) -> tuple[int, Any]:
        status, data, _ = self.transport.delete(self._protection_path())
        return status, data


# --- Pure policy layer (whitelist idempotency comparator) -------------------


def _normalized_refs(ref_name: dict[str, Any]) -> list[str]:
    include = ref_name.get("include") or []
    normalized: list[str] = []
    for ref in include:
        if ref in ("~DEFAULT_BRANCH", f"refs/heads/{DEFAULT_BRANCH}"):
            normalized.append("~DEFAULT_BRANCH")
        else:
            normalized.append(str(ref))
    return sorted(normalized)


def _normalized_exclude(ref_name: dict[str, Any]) -> list[str]:
    return sorted(str(ref) for ref in (ref_name.get("exclude") or []))


def _bypass_keys(actors: list[dict[str, Any]] | None) -> set[tuple[str, int, str]]:
    return {
        (
            str(actor.get("actor_type")),
            int(actor.get("actor_id")),
            str(actor.get("bypass_mode")),
        )
        for actor in (actors or [])
    }


def _pull_request_diff(desired: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    actual_params = actual.get("parameters") or {}
    for key, expected in PULL_REQUEST_POLICY.items():
        actual_value = actual_params.get(key)
        if expected is False and actual_value in (None, False):
            continue  # absent-false parameter is tolerated
        if actual_value != expected:
            diffs.append(
                f"pull_request.{key}: expected {expected!r}, got {actual_value!r}"
            )
    return diffs


def _status_checks_diff(desired: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    desired_params = desired.get("parameters") or {}
    actual_params = actual.get("parameters") or {}
    if actual_params.get("strict_required_status_checks_policy") is not True:
        diffs.append(
            "required_status_checks.strict_required_status_checks_policy: "
            f"expected True, got {actual_params.get('strict_required_status_checks_policy')!r}"
        )
    desired_contexts = {
        entry.get("context")
        for entry in (desired_params.get("required_status_checks") or [])
    }
    actual_contexts = {
        entry.get("context")
        for entry in (actual_params.get("required_status_checks") or [])
    }
    if desired_contexts != actual_contexts:
        diffs.append(
            "required_status_checks.contexts: expected "
            f"{sorted(desired_contexts)!r}, got {sorted(actual_contexts)!r}"
        )
    # integration_id absent-vs-null is tolerated per entry (whitelist).
    return diffs


def _rules_diff(
    desired_rules: list[dict[str, Any]], actual_rules: list[dict[str, Any]]
) -> list[str]:
    diffs: list[str] = []

    def by_type(rules: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            grouped.setdefault(rule.get("type", ""), []).append(rule)
        return grouped

    desired_by_type = by_type(desired_rules)
    actual_by_type = by_type(actual_rules)
    for rule_type, desired_list in desired_by_type.items():
        actual_list = actual_by_type.get(rule_type, [])
        if not actual_list:
            diffs.append(f"rule {rule_type}: missing")
            continue
        if rule_type == "pull_request":
            diffs.extend(_pull_request_diff(desired_list[0], actual_list[0]))
        elif rule_type == "required_status_checks":
            diffs.extend(_status_checks_diff(desired_list[0], actual_list[0]))
        # Rules without parameters (non_fast_forward, deletion) only need presence.
    for rule_type in actual_by_type:
        if rule_type not in desired_by_type:
            diffs.append(f"rule {rule_type}: unexpected extra rule")
    return diffs


def ruleset_diff(desired: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Whitelist-based diff between desired and actual ruleset payloads.

    Compares only the policy-relevant fields from library/policy.md and
    ignores all API metadata (see METADATA_FIELDS). Returns an empty list
    when the actual payload already matches the desired policy.
    """
    diffs: list[str] = []
    if actual.get("enforcement") != desired.get("enforcement"):
        diffs.append(
            f"enforcement: expected {desired.get('enforcement')!r}, "
            f"got {actual.get('enforcement')!r}"
        )
    if actual.get("target") != desired.get("target"):
        diffs.append(
            f"target: expected {desired.get('target')!r}, got {actual.get('target')!r}"
        )
    desired_conditions = desired.get("conditions") or {}
    actual_conditions = actual.get("conditions") or {}
    desired_ref = desired_conditions.get("ref_name") or {}
    actual_ref = actual_conditions.get("ref_name") or {}
    if _normalized_refs(actual_ref) != _normalized_refs(desired_ref):
        diffs.append(
            f"conditions.ref_name.include: expected {desired_ref.get('include')!r}, "
            f"got {actual_ref.get('include')!r}"
        )
    if _normalized_exclude(actual_ref) != _normalized_exclude(desired_ref):
        diffs.append(
            f"conditions.ref_name.exclude: expected {desired_ref.get('exclude')!r}, "
            f"got {actual_ref.get('exclude')!r}"
        )
    if _bypass_keys(actual.get("bypass_actors")) != _bypass_keys(
        desired.get("bypass_actors")
    ):
        diffs.append(
            f"bypass_actors: expected {desired.get('bypass_actors')!r}, "
            f"got {actual.get('bypass_actors')!r}"
        )
    diffs.extend(_rules_diff(desired.get("rules") or [], actual.get("rules") or []))
    return diffs


def plan_changes(
    desired: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Compute the change plan: create/update/none per desired ruleset."""
    changes: list[dict[str, Any]] = []
    for wanted in desired:
        matches = [a for a in actual if a.get("name") == wanted.get("name")]
        if not matches:
            changes.append(
                {
                    "name": wanted["name"],
                    "action": "create",
                    "differences": [],
                    "ruleset_id": None,
                }
            )
            continue
        current = matches[0]
        diffs = ruleset_diff(wanted, current)
        changes.append(
            {
                "name": wanted["name"],
                "action": "update" if diffs else "none",
                "differences": diffs,
                "ruleset_id": current.get("id"),
            }
        )
    return changes


def ruleset_payload_for(name: str) -> dict[str, Any]:
    for ruleset in RULESETS:
        if ruleset["name"] == name:
            return ruleset
    raise ApplyAbortError(f"unknown ruleset name {name!r}")


def verify_returned(returned: dict[str, Any], payload: dict[str, Any]) -> None:
    """Verify a created/updated ruleset is active with the exact expected policy."""
    if returned.get("enforcement") != "active":
        raise ApplyAbortError(
            f"ruleset {returned.get('name')!r} is not active after apply "
            f"(enforcement={returned.get('enforcement')!r})"
        )
    diffs = ruleset_diff(payload, returned)
    if diffs:
        raise ApplyAbortError(
            f"ruleset {returned.get('name')!r} does not match policy after apply: "
            f"{', '.join(diffs)}"
        )


# --- Orchestrator -----------------------------------------------------------


def compute_plan(api: GithubApi) -> list[dict[str, Any]]:
    actual = [api.get_ruleset(summary["id"]) for summary in api.list_rulesets()]
    return plan_changes(RULESETS, actual)


def apply_plan(
    api: GithubApi, changes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply Ruleset A then B (creating or updating), verify both active with
    the exact expected rules, and only then remove the legacy classic branch
    protection (404 treated as already removed). Aborts on any failure with
    no further mutations after the failing point.
    """
    applied: list[dict[str, Any]] = []
    for change in changes:
        name = change["name"]
        payload = ruleset_payload_for(name)
        if change["action"] == "none":
            applied.append({"name": name, "action": "none"})
            continue
        if change["action"] == "create":
            returned = api.create_ruleset(payload)
            action = "create"
        elif change["action"] == "update":
            returned = api.update_ruleset(change["ruleset_id"], payload)
            action = "update"
        else:
            raise ApplyAbortError(
                f"unknown change action {change['action']!r} for {name}"
            )
        verify_returned(returned, payload)
        applied.append({"name": name, "action": action})

    status, _ = api.delete_branch_protection()
    if status == 404:
        classic: dict[str, Any] = {"deleted": False, "already_removed": True}
    elif status in (200, 204):
        classic = {"deleted": True, "already_removed": False}
    else:
        raise ApplyAbortError(
            f"could not remove legacy classic branch protection: HTTP {status}"
        )
    return applied, classic


def run_orchestrator(
    api: GithubApi,
    *,
    mode: str,
    auth_source: str,
    owner: str,
    repo: str,
    branch: str,
) -> dict[str, Any]:
    """Execute the enforcer. `mode` is "dry-run" (never mutates) or "apply".

    Returns a structured summary suitable for both the human-readable report
    and the `--json` output. Raises ApplyAbortError on pre-mutation or verification
    failures in apply mode.
    """
    verified, missing, gate_error = api.safety_gate()
    try:
        changes = compute_plan(api)
        plan_error: str | None = None
    except EnforceError as exc:
        changes = None
        plan_error = str(exc)

    summary: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "mode": mode,
        "auth_source": auth_source,
        "rulesets": [RULESET_A["name"], RULESET_B["name"]],
        "safety_gate": {
            "verified": verified,
            "missing_contexts": missing,
            "error": gate_error,
        },
        "changes": changes,
        "no_changes": bool(
            changes is not None and all(c["action"] == "none" for c in changes)
        ),
        "applied": [],
        "classic_protection_removed": None,
    }
    if mode == "apply":
        if not verified:
            raise ApplyAbortError(
                "aborting apply: required check contexts are missing or "
                f"unverifiable on {branch} head "
                f"(missing={missing or 'none'}, error={gate_error or 'none'})"
            )
        if changes is None:
            raise ApplyAbortError(
                f"aborting apply: could not compute the change plan ({plan_error})"
            )
        summary["applied"], classic = apply_plan(api, changes)
        summary["classic_protection_removed"] = classic
    return summary


# --- Output ----------------------------------------------------------------


def policy_report_text() -> str:
    """Deterministic policy report (no timestamps, shas, or other
    non-deterministic content) so two consecutive dry-runs are identical."""
    lines = [
        "=== POLICY REPORT ===",
        f"Ruleset A: {RULESET_A['name']} (enforcement: {RULESET_A['enforcement']}, target: {RULESET_A['target']})",
        "  conditions.ref_name.include: ~DEFAULT_BRANCH",
        "  bypass_actors:",
        "    - Integration 29110 (dependabot[bot]) - bypass_mode: pull_request",
        "    - User 942000 (magnus919, sole maintainer) - bypass_mode: pull_request",
        "  rules:",
        "    - pull_request:",
        "        required_approving_review_count: 1",
        "        dismiss_stale_reviews_on_push: true",
        "        require_last_push_approval: true",
        "        require_code_owner_review: false",
        "        required_review_thread_resolution: true",
        f"Ruleset B: {RULESET_B['name']} (enforcement: {RULESET_B['enforcement']}, target: {RULESET_B['target']}, bypass_actors: none)",
        "  conditions.ref_name.include: ~DEFAULT_BRANCH",
        "  rules:",
        "    - required_status_checks:",
        "        strict_required_status_checks_policy: true",
        "        required_status_checks:",
        "          - Code Quality Gate",
        "          - Runtime Gate",
        "    - non_fast_forward",
        "    - deletion",
    ]
    return "\n".join(lines)


def render_text(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== SAFETY GATE ===")
    gate = summary["safety_gate"]
    if gate["verified"]:
        lines.append("verified: true")
        lines.append("required contexts present: " + ", ".join(REQUIRED_CHECK_CONTEXTS))
    else:
        lines.append("verified: false")
        if gate["missing_contexts"]:
            lines.append("missing contexts: " + ", ".join(gate["missing_contexts"]))
        if gate["error"]:
            lines.append("error: " + gate["error"])
    lines.append("")
    lines.append("=== CHANGE PLAN ===")
    changes = summary["changes"]
    if changes is None:
        lines.append("could not compute change plan against the live API")
    elif all(c["action"] == "none" for c in changes):
        lines.append("no changes")
    else:
        for change in changes:
            if change["action"] == "none":
                continue
            lines.append(f"- {change['name']}: {change['action']}")
            for difference in change["differences"]:
                lines.append(f"    - {difference}")
    if summary["applied"]:
        lines.append("")
        lines.append("=== APPLY RESULT ===")
        for item in summary["applied"]:
            lines.append(f"- {item['name']}: {item['action']}")
        classic = summary["classic_protection_removed"]
        if classic is not None:
            if classic.get("deleted"):
                lines.append("- classic branch protection: deleted")
            elif classic.get("already_removed"):
                lines.append("- classic branch protection: already removed (404)")
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------


def repo_from_git_remote() -> tuple[str, str] | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return None
    remote = result.stdout.strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", remote)
    if not match:
        return None
    return match.group(1), match.group(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enforce-branch-protection",
        description=(
            "Declaratively enforce the main-branch QA policy (two repository "
            "rulesets per ADR-0046). Dry-run by default; --apply is the only "
            "mode that mutates GitHub state."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="report policy, safety gate, and change plan without mutating (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply the policy: create/update rulesets, verify, remove classic protection",
    )
    parser.add_argument("--owner", help="GitHub owner (default: from git remote)")
    parser.add_argument("--repo", help="GitHub repository (default: from git remote)")
    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help=f"target branch (default: {DEFAULT_BRANCH})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single JSON document instead of the human-readable report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        token, auth_source = resolve_auth()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    owner, repo = args.owner, args.repo
    if owner is None or repo is None:
        remote = repo_from_git_remote()
        if remote is None:
            print(
                "error: could not determine owner/repo from the git remote; "
                "pass --owner and --repo",
                file=sys.stderr,
            )
            return 2
        owner = owner or remote[0]
        repo = repo or remote[1]

    mode = "apply" if args.apply else "dry-run"
    api = GithubApi(
        HttpTransport(token=token), owner=owner, repo=repo, branch=args.branch
    )

    try:
        summary = run_orchestrator(
            api,
            mode=mode,
            auth_source=auth_source,
            owner=owner,
            repo=repo,
            branch=args.branch,
        )
    except ApplyAbortError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"auth: {auth_source}")
        print(policy_report_text())
        print()
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
