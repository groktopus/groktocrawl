# Experimental runtime CI

This runbook applies only to `magnus919/groktocrawl-x`. The experimental fork is
not a replacement for mainline. Tracking: issue #7, experiment W0.

## Validation and publication are separate

[Runtime CI](../../.github/workflows/runtime.yml) runs on PRs and pushes to `main`.
It has read-only repository permissions, no provider/registry credentials and no
self-hosted jobs. GitHub-hosted runners build application and fixture images from
the checked-out source. A [CI Compose override](../../docker-compose.ci.yml) gives
those images local names, replaces the live search service with the deterministic
search fixture, and makes the model cache volume private to the Compose project.
Production Compose settings are unchanged.

The inherited [Docker Build & Publish](../../.github/workflows/docker.yml) stays
disabled, including its old integration job. It remains historical reference for
existing workflow contracts; its hardcoded upstream publishing targets must never
be enabled unchanged. The new workflow neither calls it nor waits for publication.
A later release change must define fork-owned names, credentials and release rules.

Runtime Gate requires successful change classification and every selected test job.
Missing outputs, failed classification, cancellation and skipped required jobs fail
closed. Documentation-only changes explicitly report that Docker tests were not
required. Runtime changes run the inherited critical journey, integration/service
suite and coverage gates; hosted twin selection follows the shared classifier.
The inherited exclusions remain visible in the workflow (external/observability
cases and contracts run separately on the host). Fixture success does not prove
live-provider quality or production capacity.

## First-run verification and limits

The first PR run must establish that the hosted runner can build and execute the
full stack. Integration has a 60-minute job ceiling and two concurrent Compose
build operations. Public package, base-image and model downloads still need network
access; the Docker lane is not claimed to be fully network-isolated. Search and LLM
traffic uses fixtures and no paid-provider secrets are supplied. The separate
hosted twin job enforces its existing loopback-only test restrictions.

Runtime and storage jobs prefetch a finite set of public Docker Hub fixture images
by exact digest from `mirror.gcr.io`, verify the pulled RepoDigest and image ID,
then apply the Compose reference already declared in the checkout. Other active,
non-buildable Compose images still use ordinary pulls. Compose startup uses its
local image cache so a mutable `latest` tag is not fetched again in that run. The
scale-out job seeds only its Python and HAProxy fixture images; it does not restart
Docker or touch the Actions-provisioned Valkey service. Each prefetch receipt is
uploaded as a CI artifact.

These fixture digests were checked against matching anonymous Docker Hub and cache
manifest identities on 2026-10-09 (receipt SHA-256
`a4c12eb54885cda82b99adde0415a49d9042159ca57fd69909be48bc7d5bf5d3`). In
particular, `mcuadros/ofelia:latest` is pinned to the observed digest only for this
CI fixture. The pin does not assert what `latest` will mean later or what any
operator deployment runs. Refreshing a pin requires a new identity check; ordinary
Compose references and deployment configuration remain unchanged.

Inspect build/startup failures, runner disk/RAM, model readiness, test outcomes and
coverage before accepting the lane as operational. Downloaded model capacity and
cold-start time may exceed the hosted runner's bounds; that is a failing CI setup
to resolve, not a reason to mark Runtime Gate successful or silently omit services.
Sanitized fixture diagnostics, twin provenance, test outcomes, coverage and failure
logs are uploaded; teardown removes only this job's Compose resources and volumes.

Once merged, verify workflow enablement and a docs-only and runtime-changing run.
Check actual repository rulesets separately: declaring a job named Runtime Gate
does not make it required. W0 remains incomplete until required checks and review
policy are configured and verified. Keep publishing, paid review, live calibration
and inherited self-hosted execution disabled.

## Fixture scenarios and image provenance

For an end-to-end API that cannot forward a fixture-specific search parameter,
start its search query with `[fixture:zero-results]` to select the versioned empty
result scenario. Other supported scenario names use the same fixture-only marker;
an explicit `scenario` parameter takes precedence. Ordinary queries keep their
existing fixture behavior. Never infer emptiness from nonsense query text.
Session isolation uses concurrent distinct-source operations and verifies that
deleting one session leaves the other's references, steps and artifact intact;
overlapping legitimate search results are not evidence of session leakage.

Runtime CI declares `TWIN_SEARCH_BACKEND=fixture` for provenance. SlopSearX must
then have a local image ID, a checkout-build declaration and the matching commit
SHA. The inherited live-search configuration still requires its registry digest.
Qdrant continues to require its registry digest in both modes. A missing source
record fails the evidence check rather than being relabeled as a published image.

The search fixture query-marker addition is version `v3`. Evaluation cases pin
that version and carry recomputed content hashes; their queries, scenarios, expected
outcomes and canonical narrow/broad baselines remain unchanged. Run the complete
Fast Tests suite when changing fixture versions to verify all consumers.

## Experimental storage probes

Runtime changes also run **PostgreSQL Storage Probes** using a separate hosted
runner and the [isolated storage harness](../experiments/research-postgres-harness.md).
Runtime Gate requires that job to succeed. Its database volume is independent of
the integration stack; its teardown preserves that volume until the disposable
hosted runner is discarded. The probes check database transport and persistence,
not application lifecycle correctness or backup recovery.
