# Reviewed isolated screening runner

Runtime implementation commit: `abdebda` (plus diagnostic parent `a1913c3`).
Reviewed runtime SHA-256: `347e422b1d879b395473b65b5b853be18b1ffc749e55e7ff6b0a00edc4f01298`.
Canonical backend descriptor SHA-256: `a65890d9119708bf4c406a634f6baa3131aa03c82770c6ea166fa9d65a50a1f8`.
Helper SHA-256: `45885f6d9ad453121306dd27d364b0dbaedb38134bac299a21eca4661a75b3a9`.
Gateway config SHA-256: `38336145082680fb0a103de8f84628564e3f59ca4d8317f3d8af8f2557588a65`.
Independent implementation review: no open P1/P2. 95 focused offline tests, Ruff,
and five-module mypy pass. The installed-worker probe matches this exact helper:
all synthetic 200/400/422/429/503 responses use one policy fixture read and one
provider fixture request with cap256, temperature0.3 and existing attribution tag;
zero real provider calls and zero real socket connection attempts. Normal-auth
configuration/policy preflight succeeds. No heldout queries/labels received and no
live screening dispatched. Earlier protocol/handoff blockers are superseded here.

## Isolated execution and unchanged limits

Each model RPC uses a detached Python Router process in the existing gateway
container/image. It starts no proxy app, database client, listener or new identity.
Provider credentials remain in existing configured server-side process environment.
The original client key is used only through normal server authorization; no master
credential is substituted. A preflight self-key/self-user report supplies a private
binding and original-key fingerprint held only in operator memory, stripped before
any descriptor/journal export. No credential file, authentication-table copy or
new Docker resource is created. Each child exits after its RPC, with a 15-second
hard process watchdog; SDK closure does not claim upstream cancellation.

Before every provider dispatch, one existing self-user report freshly authenticates
the original identity and correlates its current key record in memory. Both key and
user model authorization and all recognized policy restrictions are checked. Unknown
fields, changed identity/policy/configuration, expiry/revocation, a new applicable
budget/rate/route restriction, failed verification or ambiguity stop before dispatch.
Observed key/user budgets, TPM/RPM, expiry and other scope bindings are null/empty;
free is explicitly allowed. The configured global parallel limit50 uses local_only
cache operations; the pilot's model lock permits one concurrent operation. Its12k
request-byte bound is stricter than configured64MiB. The free entry and provider
route are preserved, including existing attribution. The child alone sets
parameter-dropping false. It uses the installed local pricing map and disables
library telemetry; proxy observability startup is absent and pilot journals retain
usage/provenance. Production process/configuration remain unchanged.

The final SDK transport verifies the merged model/cap/sampler/body/endpoint, bounds
bytes and permits at most one actual provider request. Policy read1 replaces the
former model-gateway HTTP request1, so each model RPC still costs at most2 HTTP
operations including provider1. Ceilings stay six attempts (two cases, three arms,
one repetition), 12 search RPCs,24 acquisitions,24 model RPCs:60 logical operations,
120 total application HTTP /84 Internet HTTP,6144 requested output tokens,288k
serialized model-input bytes,45 seconds per attempt and300 seconds per experiment.
TCP/TLS/DNS and existing observability are outside application HTTP counts. Actual
billing is not assumed zero. Unknown downstream outcomes globally stop further
work and are not replayed. Known failed responses retain typed receipts. Failures
and undispatched slots remain in the six-slot denominator.

## Parent handoff

Parent supplies only a packet with schema_version `w12.4-first-screen-queries/1`,
experiment_id, frozen_runner_sha256 equal to the bundle above, and exactly two cases
containing only case_id/question (question max4096characters). Parent retains labels,
expected facts, source ground truth and evaluation code in its separate workspace.
No candidate edits after query exposure until grading closes.

Materialize admission-template.json by reconstructing runtime_files as the mapping
of path to sha256 from runtime-freeze.json records. Parent attests the three boolean
fields; they remain false in the template, so it cannot start a run accidentally.
Canonical backend hash above is compared before questions are read. The committed
backend file is a public summary, not the bytes to hash for admission. No private
identity/fingerprint is required from the parent. The original four unresolved
source hashes remain in the template and are never repeated.

Exports: exclusive `run-manifest.jsonl`, `dispatches.jsonl`,
`backend-receipts.jsonl`, `attempts.jsonl`. They include six scheduled slots,
per-operation reservations/receipts, exact answers, captured sources and hashes,
usage/failure provenance, and parent-pending grading. They contain no credentials
or authentication records. Baseline arm means current upstream deep loop under
the common restricted acquisition profile, not uncapped deployed performance.
Other arms are fixed focused control and existing W12.4 obligation-bounded
continuation; materiality/closure remain provisional until parent verification.
No category benefit or runtime promotion is established.

## CI evidence and diagnostic limitation

Run40538348585 at e4a0ee6 is TERMINAL FAILURE. Hosted research-runtime integration,
PostgreSQL storage, protected profiles, twins and their research gate passed. Main
integration:2842passed,8failed,162skipped; failures seed scraped refs before deepen
behavior. Original source-failure receipts were not retained, so their actual source
cause remains unknown. A separate confirmed provenance defect inspected service
names instead of running containers when Compose image inventory was empty.
Commit a1913c3 adds typed sanitized session failures and fixes actual running-image
provenance, retaining all criteria/assertions/fixtures.78 targeted diagnostic tests
and46 final focused tests passed; independent review found no P1/P2. These changes
do not claim to fix the8source-seeding failures. New-head CI must be tracked separately.
Prior storage-prefetch failure did not recur: unchanged pins fetched and verified;
old discarded stderr cannot support a retrospective registry/daemon cause. No blind
rerun, cancellation, runner-setting change or gate weakening occurred.
