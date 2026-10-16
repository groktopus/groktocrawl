# Runbook: Self-Hosted Runner Fork-PR Protection (#562)

Status: **IN-REPO CONTROLS SHIPPED — PLATFORM-LEVEL (ORG) REMEDIATION DEFERRED BY
MAINTAINER DECISION, RESIDUAL RISK ACCEPTED.**

This runbook is the canonical in-repo record of how the self-hosted runner lane
(`runs-on: [self-hosted, groktopus, docker]`) is protected against untrusted fork
pull requests, what remains deferred at the org level, and the exact steps to
close that gap. Per ADR-0056 ("Required Protected Configuration"), every
in-repo mechanism described here is **defense in depth only — not a security
boundary**.

## Why fork PRs are special

On `pull_request` events GitHub runs workflows from the PR **merge ref**
(the merge of the PR head into the base). Consequences:

- A fork can modify `.github/workflows/docker.yml` itself; its rewritten
  version executes on our infrastructure for that event.
- Any in-repo guard (an `if:` condition, a detection step) can be stripped by
  that same edit before it evaluates.
- Therefore no check that lives inside the workflow can be authoritative.

## What is shipped in-repo (defense in depth)

1. **Job-level fork guard** on `integration-tests` (`.github/workflows/
   docker.yml`): the self-hosted integration job runs only when
   `format('{0}', github.event.pull_request.head.repo.fork) == 'false'`
   or the event is a push to main/tag. Best-effort: bypassable by editing
   the workflow file. Null guard (corrected 2026-08-24, scrutiny round 1 on
   #562): loose equality coerces Null->0 and Boolean false->0, so a plain
   `fork == false` was TRUE for deleted forks (null), admitting them onto
   the lane; the string rendering discriminates exactly ('true'/'false'/''),
   keeping null fail-closed OFF the self-hosted runner.
2. **Fail-fast workflow-edit detector** (#562): inside the unskippable
   `changes` classification job, the step
   `Detect fork PR modifying GitHub workflows` runs
   `scripts/ci_fork_pr_guard.py` on every pull_request event whose condition
   arms via the explicit disjunction
   `(head.repo.fork == true || head.repo.fork == null)` — required because
   GHA loose equality makes `false == null` TRUE too, so the disjunction
   matches every pull_request event and the SEAM does the trust decision:
   it skips only when the rendered `CI_PR_FORK` is the literal string
   "false" (a proven same-repository PR); a null fork renders as an empty
   string and stays fail-closed. If any changed path is under
   `.github/workflows/**`, the script exits non-zero with an explanatory
   message, failing the classification job and tripping runtime-gate's
   fail-closed branch — the required checks go red as a paper trail.
3. **Runtime-gate fail-closed handling**: a missing/skipped
   `integration-tests` result fails Runtime Gate for runtime PRs, and fork
   PRs get an explicit "integration skipped for security" summary instead of
   silent success. The summary/exclusion conditions use the string-rendered
   comparison (`format('{0}', head.repo.fork) != 'false'`), so deleted forks
   (null) get the same explicit treatment as proven forks rather than a
   generic gate failure.

The decision logic lives in an executable seam so the three scenarios can be
simulated locally:

```bash
# 1. fork + workflow path -> exit 1 (detection fires)
printf '.github/workflows/docker.yml\n' | python3 scripts/ci_fork_pr_guard.py \
  --event-name pull_request --fork true; echo "exit=$?"
# 2. same-repo + workflow path -> exit 0
printf '.github/workflows/docker.yml\n' | python3 scripts/ci_fork_pr_guard.py \
  --event-name pull_request --fork false; echo "exit=$?"
# 3. fork + non-workflow path -> exit 0
printf 'agent-svc/agent/api.py\n' | python3 scripts/ci_fork_pr_guard.py \
  --event-name pull_request --fork true; echo "exit=$?"
# 4. deleted fork (null renders empty) + workflow path -> exit 1 (fail closed)
printf '.github/workflows/docker.yml\n' | python3 scripts/ci_fork_pr_guard.py \
  --event-name pull_request --fork ''; echo "exit=$?"
```

## Platform-level remediation (AUTHORITATIVE control — currently DEFERRED)

> The maintainer has decided to defer these org-level changes; the residual
> risk described below is accepted until this section is executed. Revisit
> whenever the runner group's exposure to public repositories matters more
> than the operational friction.

Perform all three steps in the GitHub org settings for **groktopus**:

1. **Deny public repositories for the runner group used by the lane.**
   Org Settings → Actions → Runners → (runner group containing
   `[self-hosted, groktopus, docker]`, e.g. "groktopus") → edit the group →
   set **"Repository access"** to *Selected repositories* and/or uncheck
   **"Allow public repositories"** — the API field is
   `allows_public_repositories=false` on the runner group
   (`PATCH /orgs/{org}/actions/runners/groups/{id}` or the equivalent UI
   toggle). Without this, any public/fork repository that can trigger a
   workflow referencing `runs-on: [self-hosted, groktopus, docker]` may
   reach the runner.
2. **Optionally restrict the group to selected workflows.**
   Same runner-group editor: enable **"Restrict to selected workflows"**
   (`restricted_to_workflows=true`) and allowlist the repo's own workflow
   files (e.g. `.github/workflows/docker.yml`,
   `.github/workflows/answer-evals.yml`). This bounds what a compromised or
   malicious workflow file can ask the runner group to execute.
3. **Require approval for outside-contributor workflow runs.**
   Org Settings → Actions → General → Fork pull request workflows →
   **"Require approval for all external contributors"** (the strictest of
   the three options; the default only requires approval for first-time
   contributors). With this set, a workflow run triggered by an external
   contributor stays queued until an org maintainer approves it — including
   re-runs after their initial approval window expires per policy.

Steps 1–3 together are the non-bypassable control: even if a fork strips
every in-repo guard, GitHub itself refuses to place the job on the
self-hosted runner (step 1), refuses to run unallowlisted workflows there
(step 2), or holds the run for manual approval (step 3).

## Residual risk while deferred

A fork pull request that edits `.github/workflows/**` executes its edited
workflow from the merge ref. The in-repo detector makes that loud (red
checks), but cannot stop a workflow that removes the detector itself before
running. Until the org-level steps above are applied:

- Treat any red "Detect fork PR modifying GitHub workflows" step as a
  security-relevant signal, and review who authored the PR before touching
  the runner host.
- The runner host (`deployment.example.internal`) must assume hostile input from PR-triggered jobs:
  keep the CI fixture compose project isolated, never store long-lived
  secrets in the runner environment beyond the ephemeral `GITHUB_TOKEN`.

## Verification tooling

- `python3 scripts/enforce-branch-protection.py --verify-rulesets` — read-only
  (GET-only) assertion that both main rulesets ("main review policy",
  id 20314008; "main required checks", id 20314768) are still
  `enforcement == active` and diff-clean against the expected-state constants
  in the script. Exit 0 = both healthy; nonzero = drift, missing, inactive,
  or partial verification. Repo-scoped token suffices; no admin:org needed.
- `tests/service/test_fork_guard_contract.py` — Fast Tests coverage of the
  three detector scenarios and the workflow wiring.
