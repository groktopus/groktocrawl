# Emergency Branch Protection Bypass

Owner: GroktoCrawl maintainers

## Severity

High

## Purpose

`main` is protected by two repository rulesets (see ADR-0046): `main review policy` (one approving review for humans) and `main required checks` (`Code Quality Gate` + `Runtime Gate`, strict up to date). The policy is enforced on admins — there is **no routine bypass**. This runbook is the deliberate, audited exception path for a genuine emergency where a merge to `main` must happen and the normal review/CI path cannot complete in time.

Use it **only** when:

- A legitimate fix must land on `main` immediately (e.g., a production incident fix), and
- The blocking condition (failed required check) cannot be resolved within the required window.

Never use it for routine merges. Admins are subject to the policy; the audit trail below records every lift.

> **Update (2026-08-03): maintainer self-merge amendment.** Per ADR-0046 (2026-08-03
> amendment, "allow maintainer to merge own PRs (review bypass only)"), the sole
> maintainer `magnus919` is a User bypass actor (`bypass_mode: pull_request`) on
> Ruleset A `main review policy`, so they can merge their **own** green PRs without
> an approving review (GitHub disallows self-approval). After this amendment, this
> emergency path is needed **only when required checks fail** — a `Code Quality Gate`
> or `Runtime Gate` failure that cannot be resolved in time. A missing review on the
> maintainer's own PR is no longer an emergency: the normal merge flow (with green
> required checks) handles it. The amendment does **not** relax required checks:
> Ruleset B `main required checks` has no bypass actors, so merging a PR whose
> required checks fail still requires this emergency path.

## Timeline

1. Resolve the two ruleset ids and capture the "before" audit evidence.
2. Disable **both** rulesets (`main review policy` and `main required checks`).
3. Verify both are `disabled`.
4. Merge the blocking change through the normal flow (GitHub UI merge button or `gh pr merge --merge`).
5. Restore **both** rulesets to `active` **within 24 hours**.
6. Verify both are `active` again.
7. Capture the "after" audit evidence and record the incident in a tracking issue.

## Procedure

### 1. Resolve ruleset ids and capture "before" audit evidence

```bash
RULESET_A_ID="$(gh api repos/groktopus/groktocrawl/rulesets --jq '.[] | select(.name == "main review policy") | .id' | head -n 1)"
RULESET_B_ID="$(gh api repos/groktopus/groktocrawl/rulesets --jq '.[] | select(.name == "main required checks") | .id' | head -n 1)"
echo "A=$RULESET_A_ID B=$RULESET_B_ID"
```

Both ids must be non-empty before continuing. Capture the pre-lift audit trail:

```bash
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_A_ID}/history"
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_B_ID}/history"
```

### 2. Disable both rulesets

```bash
echo '{"enforcement": "disabled"}' | gh api -X PUT "repos/groktopus/groktocrawl/rulesets/${RULESET_A_ID}" --input -
echo '{"enforcement": "disabled"}' | gh api -X PUT "repos/groktopus/groktocrawl/rulesets/${RULESET_B_ID}" --input -
```

### 3. Verify both are disabled

```bash
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_A_ID}" --jq '.name + ": " + .enforcement'
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_B_ID}" --jq '.name + ": " + .enforcement'
```

Expected output:

```
main review policy: disabled
main required checks: disabled
```

### 4. Merge the blocking change

Merge through the normal flow: the GitHub UI merge button, or `gh pr merge --merge` (replace `<pr-number>` with the PR being merged):

```bash
gh pr merge <pr-number> --merge
```

### 5. Restore both rulesets to active

Restore **immediately after the merge** — the rulesets must be `active` again within 24 hours:

```bash
echo '{"enforcement": "active"}' | gh api -X PUT "repos/groktopus/groktocrawl/rulesets/${RULESET_A_ID}" --input -
echo '{"enforcement": "active"}' | gh api -X PUT "repos/groktopus/groktocrawl/rulesets/${RULESET_B_ID}" --input -
```

### 6. Verify both are active

```bash
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_A_ID}" --jq '.name + ": " + .enforcement'
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_B_ID}" --jq '.name + ": " + .enforcement'
```

Expected output:

```
main review policy: active
main required checks: active
```

### 7. Capture "after" audit evidence and record the incident

```bash
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_A_ID}/history"
gh api "repos/groktopus/groktocrawl/rulesets/${RULESET_B_ID}/history"
```

The history endpoint records the actor and timestamp for each ruleset version (the disable and the restore both appear). Create a tracking issue:

```bash
gh issue create \
  --title "Emergency branch protection bypass on main (rulesets disabled on $(date -u +%Y-%m-%d))" \
  --body "Temporarily disabled 'main review policy' and 'main required checks' on main to merge a blocking change, then restored to active within 24h. Audit evidence: ruleset history for both ruleset ids (RULESET_A_ID / RULESET_B_ID)."
```

## Audit trail

The per-repo audit record is the ruleset history endpoint: `GET /repos/{owner}/{repo}/rulesets/{id}/history` for each ruleset id, captured **before** the lift (step 1) and **after** the restore (step 7). The org audit-log REST API (`GET /orgs/groktopus/audit-log`) is unavailable on this org — it is Enterprise-only — so ruleset history is the authoritative evidence. Attach both captures to the tracking issue.
