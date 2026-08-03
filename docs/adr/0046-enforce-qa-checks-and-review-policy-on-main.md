# Enforce QA Checks and Review Policy on main

* Status: accepted
* Deciders: GroktoCrawl maintainers
* Date: 2026-08-03

## Context and Problem Statement

The `main` branch currently has a partial classic branch-protection rule:
it requires **0** approving reviews, has no required status checks, and does
not require resolved review conversations. The Docker Runtime Gate and the
Code Quality results are therefore advisory at the merge boundary — a merge
can land with failing QA or no review. Issue #499 asks to make these
enforceable release conditions: every merge to `main` must pass the stable
QA gates and receive an approving human review.

## Decision Drivers

* QA signals must be **enforced, not advisory**: the aggregate gates
  (`Code Quality Gate`, `Runtime Gate`) must block merges that fail them.
* Required checks must be **stable aggregate gates only** — never volatile
  matrix or per-tool jobs that appear and disappear between runs.
* Automation must keep shipping: `dependabot[bot]` should not need a human
  approving review, but must still pass the required checks. Release-please
  PRs are NOT exempt (2026-08-03 amendment): `github-actions[bot]`, the
  actual author of release-please PRs, cannot be a ruleset bypass actor, so
  release-please PRs require a human approving review.
* The sole maintainer can merge their own PRs (2026-08-03 amendment):
  GitHub disallows self-approval, so with only one org member an approving
  review can never exist for `magnus919`'s own PRs. The maintainer is
  therefore a User bypass actor with `bypass_mode: pull_request` — review
  bypass only; required checks still bind via Ruleset B.
* The policy is **enforced on admins**: no routine bypass path.
* The change must be **auditable** (who changed enforcement, when) and have a
  documented emergency exception path.
* The enforcement mechanism must be supported on a public repository on any
  GitHub plan.

## Considered Options

* **A. Repository rulesets (two)** — GitHub's recommended mechanism. Supports
  bypass actors (classic protection's API support is limited), versioned
  ruleset history provides a per-repo audit trail, and enforcement toggling is
  a clean audited emergency lever. Public repos have rulesets on any plan.
  Requires a two-ruleset split because `bypass_actors` is ruleset-wide.
* **B. Classic branch protection only** — existing mechanism, but its API
  support for bypass actors is limited, it has no versioned history endpoint
  for auditing, and it cannot express "bots skip review but not checks" as
  cleanly. Rejected.
* **C. Single combined ruleset** — would list the bots as bypass actors and
  carry both the review rule and the required-checks rule. Because a bypass
  actor is exempt from the **entire** ruleset, the bots would skip the
  required checks too, letting automation merge with failing QA. Rejected.
* **D. Do nothing** — keeps QA advisory and reviews optional. Rejected: that
  is the problem being fixed.

## Decision Outcome

Enforce the policy on `main` with **two active repository rulesets**
(option A), applied declaratively and idempotently by
`scripts/enforce-branch-protection.py`:

**Ruleset A — `main review policy`** (enforcement: `active`, target: the
default branch, `conditions.ref_name.include: ["~DEFAULT_BRANCH"]`):

* `pull_request` rule with parameters:
  * `required_approving_review_count: 1` — at least one approving review for
    non-automation changes,
  * `dismiss_stale_reviews_on_push: true` — stale approvals are dismissed
    after subsequent pushes,
  * `require_last_push_approval: true`,
  * `require_code_owner_review: false`,
  * `required_review_thread_resolution: true` — open review conversations
    block merge.
* `bypass_actors` (exactly two, review-requirement exemption from the review
  rule **only**; both `bypass_mode: "pull_request"`):
  * `{actor_type: "Integration", actor_id: 29110, bypass_mode: "pull_request"}`
    — `dependabot[bot]`.
  * `{actor_type: "User", actor_id: 942000, bypass_mode: "pull_request"}`
    — `magnus919`, the sole maintainer.
  * **Amendment (2026-08-03, user-approved):** the sole maintainer
    `magnus919` (user 942000) is a User bypass actor with `bypass_mode:
    pull_request` so they can merge their OWN PRs without an approving
    review (GitHub disallows self-approval). Scope is REVIEW BYPASS ONLY:
    the PR requirement is preserved (the bypass applies only within a PR
    context; direct pushes to `main` remain blocked by the `pull_request`
    rule) and Ruleset B (`main required checks`, no bypass actors) still
    requires `Code Quality Gate` + `Runtime Gate` on every PR including the
    maintainer's. The emergency exception path is now needed only when
    required checks fail.
  * **Earlier amendment (2026-08-03, user-approved):** the release-please
    bypass exemption is dropped. `github-actions[bot]` — the actual author
    of release-please PRs (user id 41898282) — cannot be added to a ruleset
    `bypass_actors` list (GitHub blocks the GitHub Actions app identity by
    design), and the Release Please GitHub App (40688) is deprecated
    (turndown 2025-08-13) and not installed on org `groktopus`.
    Consequence: release-please PRs require a human approving review (the
    sole org member `magnus919` can approve bot-authored PRs).

**Ruleset B — `main required checks`** (enforcement: `active`, target: the
default branch, **no bypass actors**):

* `required_status_checks` rule with parameters:
  * `strict_required_status_checks_policy: true` — branches must be up to
    date before merging,
  * `required_status_checks` exactly `[{context: "Code Quality Gate",
    integration_id: null}, {context: "Runtime Gate", integration_id: null}]`.
* `non_fast_forward` — force-pushes stay blocked,
* `deletion` — branch deletion stays blocked.

The legacy classic branch-protection rule on `main` is removed only after
both rulesets are confirmed active (most-restrictive-wins aggregation makes
the classic rule redundant once the rulesets are live).

## Consequences

* Merges to `main` now require both stable aggregate gates to pass and a
  human approving review (with stale approvals dismissed and resolved
  conversations); the policy binds repo admins too.
* **Why two rulesets:** `bypass_actors` exempts an actor from the entire
  ruleset. The split keeps `dependabot[bot]` and the sole maintainer
  `magnus919` (user 942000) exempt from the human review only; everyone —
  bots and admins — must still pass the required checks in Ruleset B.
  Release-please PRs (authored by `github-actions[bot]`, which cannot be a
  ruleset bypass actor) require a human approving review per the 2026-08-03
  amendment.
* **Emergency exception path:** an org admin may temporarily set both
  rulesets' enforcement to `disabled`, merge the blocking change, and restore
  both to `active` within 24 hours, recording the incident in a tracking
  issue. See `docs/runbooks/emergency-branch-protection-bypass.md`. After
  the 2026-08-03 maintainer-self-merge amendment, the emergency path is
  needed only when the maintainer must merge a PR whose required checks fail
  (the maintainer can already merge their own green PRs without approval;
  Ruleset B has no bypass actors).
* **Audit trail:** `GET /repos/{owner}/{repo}/rulesets/{id}/history` records
  the actor and timestamp per ruleset version. The org audit-log REST API
  (`GET /orgs/{owner}/audit-log`) is Enterprise-only and unavailable on this
  org, so ruleset history is the per-repo audit record.
* **Known consequence for already-open PRs:** PRs #510, #511, and #420 were
  branched before the `Code Quality Gate` job existed. Once the ruleset is
  active they may show "Expected — Waiting for status" for the new required
  context until they are updated onto new `main`. The enforcement script's
  pre-apply safety gate verifies both required contexts exist in live check
  runs on the current `main` head so newly required contexts never dead-end
  merges.

## Links

* GitHub issue [#499](https://github.com/groktopus/groktocrawl/issues/499)
* [Emergency Branch Protection Bypass](../../runbooks/emergency-branch-protection-bypass.md)
