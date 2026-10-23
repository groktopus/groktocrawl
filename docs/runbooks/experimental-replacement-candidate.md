# Retained candidate deployment topology

The research architecture developed in GroktoCrawl X was adopted into canonical
GroktoCrawl in [ADR-0069](../adr/0069-adopt-bounded-research-substrate.md) and
[v1.0.0](https://github.com/groktopus/groktocrawl/releases/tag/v1.0.0).
This runbook maintains the isolated deployment topology introduced during that
experiment. Its `groktocrawl-x-candidate` project name and `candidate-*` service
names are retained compatibility identifiers, not a statement that the running
application comes from the former X repository.

For a new standard deployment, use `docker-compose.yml` and the
[mainline migration runbook](mainline-architecture-migration.md) for its storage
prerequisites. For an existing candidate-topology deployment, preserve its
resolved configuration, project identity, networks, volumes, database, and
published ports. Applying this runbook does not switch an existing deployment
to the standard topology.

## Where the legacy names come from

`compose.experimental-candidate.yml` sets `name: groktocrawl-x-candidate` and
names its API service `candidate-agent`. With those defaults, Compose generates
`groktocrawl-x-candidate-candidate-agent-1`. The update command below also
passes `-p groktocrawl-x-candidate` explicitly. Compose chooses `-p` before
`COMPOSE_PROJECT_NAME`, then the top-level `name` from its loaded files; a
configured `container_name` can replace the generated container name.

A promoted deployment can load private generated Compose files outside Git
rather than this template. Record the launch arguments and actual file paths,
then reconcile their project name and service definitions with Docker's Compose
labels. Do not assume a historical checkout is still an active input. Updating
the checked-in template cannot rename a deployment that keeps an explicit `-p`
argument or a private resolved file. This repository does not provide a generator
for operator-maintained `promotion.private.json` or launch `command.json` files.
Keep those files private: resolved configuration can contain credentials.

The same file points the candidate portal's `AGENT_BASE_URL` and the MCP
transport's `GROKTOCRAWL_URL` at `http://candidate-agent:8080`.
`compose.capture-https.yml` extends that service, and
`deploy/capture-https.cfg` resolves `candidate-agent:8080` through Docker DNS.
These are connected deployment contracts. Changing only a container or service
name can break internal routing; changing the project name can select different
networks and named volumes rather than the existing data.

Identify application source by its image digest and OCI source/revision labels,
not the container name. Compare the image's platform manifest/config digest with
the release provenance; a Docker image ID is a config digest, not a multi-platform
index digest. If labels are missing or overridden, reconcile the exact build
revision and private resolved deployment configuration before attributing source.
The [v1.0.1 provenance](https://github.com/groktopus/groktocrawl/releases/download/v1.0.1/release-image-provenance.json)
records canonical source, revision, and platform digests for all ten release images.

Renaming an existing deployment is a separate operator-led migration: inventory
project/service identities, external network attachments, proxy/MCP references,
ports and persistent stores; verify backups and rehearse cutover and rollback
in isolation. Do not rename the project or services, replace volumes, or use
`docker compose down -v` as a cosmetic cleanup. Archiving the former X source
repository does not stop containers or make their stored data disposable.

The first candidate uses PostgreSQL as the authority for retained research
artifacts and pgvector for semantic serving. Qdrant remains in the candidate as
an unchanged rollback target until the migration and rollback rehearsal in W9
is complete. The incumbent deployment is not a rollback target and is never
mutated by this procedure.

## Updating an existing candidate

For an existing deployment, use its active resolved Compose configuration, not
an older sample environment file. A global image-tag change can unintentionally
replace services that are meant to stay pinned. Preserve credentials, ports,
networks and every dependency image. Keep the resolved configuration and prior
image identities in private, durable storage outside the repository.

The [October 15 release check](../experiments/release-readiness-2026-10-15.md)
updates only `candidate-agent`, `candidate-mcp` and the installed CLI. It makes
no database migrations and does not change the search engine. Its retained-data
checks use schema 16; the schema-14 instructions below describe the original
candidate bootstrap, not an upgrade from the current deployment.

Before updating:

1. Confirm required CI passes on the exact source revision. Build and record the
   two application images from that revision; retain the prior images.
2. Check all services are healthy and no research or crawl jobs are active.
   Record a retained workspace revision and the digest of a rendered artifact.
3. Restore PostgreSQL and Valkey backups into a separate disposable project.
   Verify the new application and the prior application can both read the
   restored data. Test rollback there before changing the live candidate.
4. Prepare a private resolved Compose configuration that changes only those two
   application images and the reported API revision. Stop admission through the
   API and MCP services, then take fresh database and cache backups. Do not
   describe independently captured live snapshots as an atomic backup.

Use explicit service names when starting the prepared application configuration:

```sh
docker compose -p groktocrawl-x-candidate -f "$CANDIDATE_RESOLVED_COMPOSE"   up -d --no-deps --wait candidate-agent candidate-mcp
```

Verify exact image and runtime revision, model identity, published ports, CLI
and MCP calls, retained artifact digests, and unchanged dependency containers.
If these checks fail, start the two prior application images using the saved
configuration and `--no-deps`. An application rollback does not require restoring
or deleting database data when the schema is unchanged and old-reader
compatibility has been verified. Retain the backups for recovery; never delete
candidate volumes as part of an application update.

Replace the CLI atomically with the complete reviewed executable, retain the
previous file, and compare source and installed hashes. Test the installed
executable itself. Existing agent configuration should not need rewriting.

## Frozen topology

| Responsibility | Candidate service | Durable state |
|---|---|---|
| Job and execution coordination | `candidate-valkey` | `candidate_valkey_data` |
| Research artifact authority | `candidate-postgres` | `candidate_postgres_data` |
| Semantic serving | `candidate-semantic` using pgvector | PostgreSQL projection |
| Semantic rollback | `candidate-qdrant-rollback` | `candidate_qdrant_rollback_data` |
| Web discovery | `candidate-slopsearx` | Valkey cache only |
| Acquisition | `candidate-scraper` and `candidate-browser` | candidate browser cache |
| HTTP research API | `candidate-agent` | PostgreSQL and Valkey above |
| Portal health dependency | `candidate-portal` | no independent durable authority |
| MCP transport | `candidate-mcp` | no independent durable authority |

Only the HTTP API and MCP transport publish ports. Both bind to `127.0.0.1` by
default. Set `CANDIDATE_BIND_IP` to a specific trusted interface, or to
`0.0.0.0` when the host's network policy is the intended boundary. When MCP is
reachable beyond loopback, list every expected Host value in
`MCP_ALLOWED_HOSTS`. The HTTP API remains protected by
`CANDIDATE_API_KEY`; the MCP transport carries that credential internally and
must be exposed only on a trusted network. The project name is fixed as
`groktocrawl-x-candidate`; every service and volume also carries a
candidate-specific name. Do not combine this file with `docker-compose.yml`.

## Prepare a clean target

Check out the exact reviewed revision on the target host. Then create private
credentials outside the repository in persistent per-user storage:

```sh
umask 077
export CANDIDATE_CONFIG="$HOME/.config/groktocrawl-x-candidate"
mkdir -p "$CANDIDATE_CONFIG"
chmod 700 "$CANDIDATE_CONFIG"
openssl rand -hex 32 > "$CANDIDATE_CONFIG/postgres-password"
openssl rand -hex 32 > "$CANDIDATE_CONFIG/api-key"
cp .env.experimental-candidate.sample "$CANDIDATE_CONFIG/candidate.env"
chmod 600 "$CANDIDATE_CONFIG/postgres-password" \
  "$CANDIDATE_CONFIG/api-key" \
  "$CANDIDATE_CONFIG/candidate.env"
```

Do not put these files under `/tmp`, `/private/tmp`, or `/var/tmp`. Compose reads
the PostgreSQL credential from its host path whenever it creates the container;
a host cleanup or restart can therefore make the stack impossible to recreate.

Edit `$CANDIDATE_CONFIG/candidate.env`:

- set `CANDIDATE_IMAGE_TAG` to the full checked-out Git revision;
- set the PostgreSQL password-file path to the file just created;
- set `CANDIDATE_API_KEY` to the value in the API-key file;
- leave `CANDIDATE_BIND_IP=127.0.0.1` for host-local use, or set the intended
  trusted interface and matching `MCP_ALLOWED_HOSTS` values;
- set the working LiteLLM TLS URL and private key for `inference.example.internal`;
- set the SlopSearX search-provider key.
- set `SEMANTIC_CPU_LIMIT` to the CPU capacity available for local embedding
  inference; the portable default is 2 cores.

The checked-in sample selects LiteLLM's `free` model alias. Keep secrets in
the private environment and credential files; never add them to a receipt or
commit them. Validate the private files before invoking Compose:

```sh
python3 scripts/validate_experimental_candidate_config.py \
  "$CANDIDATE_CONFIG/candidate.env"
```

On a genuinely clean target, this command must return no candidate resources:

```sh
docker ps -a --filter label=com.docker.compose.project=groktocrawl-x-candidate
docker volume ls --filter label=com.docker.compose.project=groktocrawl-x-candidate
```

If it lists resources, preserve them and investigate their owner. Do not delete
volumes merely to make the clean-start check pass.

## Resolve, build, and start

Use the same file and private environment for every command:

```sh
export CANDIDATE_ENV="$HOME/.config/groktocrawl-x-candidate/candidate.env"
export CANDIDATE_COMPOSE=compose.experimental-candidate.yml

python3 scripts/validate_experimental_candidate_config.py "$CANDIDATE_ENV"
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" config --quiet
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" config --services
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" build --pull
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" up -d --wait
```

There are no fixed sleeps. Compose waits for the declared health checks. On the
first start, PostgreSQL applies the ordered research migrations through schema
14, and the semantic service creates the pgvector projection before becoming
ready. The semantic health response must identify pgvector as the serving store
and Qdrant as rollback-ready.

The protected capture routes also depend on the fixed capture and model gateway
addresses and on working Docker DNS for public source hosts. A healthy API or
scraper health check does not prove that those routes can resolve or reach their
upstream services. After changing Compose IPAM ranges, recreate the affected
containers and networks with the updated Compose configuration before checking
scrape behavior; restarting containers on the old networks retains the old
address allocations. Confirm the resolver gateways and public DNS are ready.
The deterministic fixture hostname mapping belongs only to the capture
gateway; the isolated scraper must not resolve that fixture through a local
host alias. Then verify the source gateway's hostname-policy preflight and a
representative public scrape through the complete scraper path, followed by the
protected-route probes. The scraper's local resolver is intentionally isolated; a successful
preflight does not replace the proxied scrape check. API health alone is not a
capture-readiness signal.

## Verify and freeze the receipt

This verifier creates a fictional research run. Use it only on an isolated
development/demo stack with its dedicated private `$CANDIDATE_ENV`; never edit a
production environment file for this procedure. In that file, set the existing
`FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS` entry to `true` (add it if absent).
Keep it true through the entire verification and every pilot checkpoint below.
The verifier and checkpoint runner recreate services using `--env-file`, so a
flag prefixed to only the initial `docker compose up` command does not persist.

Clear any shell override so subsequent commands use that dedicated file:

```sh
unset FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" \
  up -d --no-deps --force-recreate candidate-agent
```

Keep the flag false for normal operation. Reset the dedicated file to false and
recreate the demo agent only after the entire verification/checkpoint sequence
is finished or abandoned, as described below. Existing retained reads remain
available while fixture admission is disabled.

Load only the candidate API key into the verifier process and write the receipt
outside the repository first:

```sh
export CANDIDATE_API_KEY=$(docker compose \
  --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" \
  exec -T candidate-agent printenv API_KEY)
UV_CACHE_DIR=/tmp/groktocrawl-x-uv-cache uv run --with httpx \
  scripts/verify_experimental_candidate.py \
  --env-file "$CANDIDATE_ENV" \
  --compose-file "$CANDIDATE_COMPOSE" \
  --output /tmp/groktocrawl-x-candidate-receipt.json
```

The verifier fails unless all of these complete:

- aggregate service health;
- PostgreSQL research schema 14 and the pgvector extension;
- the experimental capability document identifying PostgreSQL artifact authority;
- one admitted research run, completed artifact set, and terminal SSE replay;
- retrieval of the manifest plus summary, analysis, and dossier bytes;
- one real SlopSearX search through HTTP and through the repository CLI;
- bounded text and structured-output requests through the configured model
  provider and model alias;
- MCP initialization and the `research_capabilities` tool;
- capture of the source revision, lockfile and Compose hashes, resolved container
  image identities, and running service state.

The JSON receipt deliberately excludes environment values, credentials, private
addresses, and artifact bodies. Review it before copying it into the W9 evidence
packet.

Container health and ordinary search only prove that the API and retrieval
dependencies are reachable. They do not prove that the configured model alias
can serve research work. The two model probes are therefore readiness gates;
their receipt records only readiness, HTTP class, and latency.

## Run a time-gated pilot checkpoint

Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=true` in the same dedicated
private `$CANDIDATE_ENV` for every checkpoint and retry; do not reset it after
only the initial verifier. Each checkpoint passes this file to later Compose
recreations and to the fixture verifier.

Use the checkpoint runner for restart checkpoint 0, checkpoint 1, and checkpoint
2. It reads the tracked
pilot state, refuses an early or out-of-order checkpoint, resolves the effective
API key from the running container rather than parsing quoted environment text,
runs the compatibility and cross-client research journeys, captures a bounded
resource snapshot, and publishes the packet only after every step succeeds.

```sh
UV_CACHE_DIR=/tmp/groktocrawl-x-uv-cache uv run --no-project \
  --with httpx --with requests python \
  scripts/run_w9_pilot_checkpoint.py \
  --checkpoint 0 \
  --env-file "$CANDIDATE_ENV" \
  --compose-file "$CANDIDATE_COMPOSE" \
  --output-dir /tmp/w9-checkpoint-0
```

When the deployed candidate uses an override, pass each Compose file in
deployment order with another `--compose-file` flag. The runner and its
research verifier then inspect the effective stack and hash all supplied
Compose files. Set `CANDIDATE_IMAGE_TAG` in the runner environment to the
deployed revision when the private env file still names an older tag.

After a passed restart checkpoint, use `--checkpoint 1` and then
`--checkpoint 2`, each with a new output directory. A
failure packet is retained beside the requested output path with a `.failed-*`
suffix. Do not rename it into a successful packet or advance the tracked pilot
state. Before publication, run the resource sanitizer from a current-main
checkout against the successful output directory:

```sh
python scripts/sanitize_w9_resource_receipt.py /path/to/w9-checkpoint-output
```

It verifies the runner's original receipt digests, strips container IDs,
container and image names, port mappings, host limits, and I/O counters, and
updates the checkpoint digests. The public packet retains normalized healthy
service labels plus CPU and memory percentages. Then review all receipts for
private data, copy the sanitized packet into the W9 evidence directory, update
`w9-pilot-state.json`, and commit them together. Never publish raw Compose
details or Docker stats.

If a candidate image or configuration changes, end the old window and clear
its active timing gates in the tracked state. Before rerunning checkpoint 0,
record the new exact runtime and configuration baseline plus its start time;
the runner intentionally rejects a state with no start time. Prior successful
requests remain historical evidence, not credit toward the new window.

After checkpoint 2 and the tracked-state update, verify the entire frozen
window before writing or accepting the replacement decision:

```sh
python scripts/verify_w9_closeout.py \
  --checkpoint-dir docs/experiments/evidence/replacement-rehearsal/<checkpoint-0> \
  --checkpoint-dir docs/experiments/evidence/replacement-rehearsal/<checkpoint-1> \
  --checkpoint-dir docs/experiments/evidence/replacement-rehearsal/<checkpoint-2> \
  --output docs/experiments/evidence/replacement-rehearsal/w9-closeout-verification.json
```

The verifier fails closed unless all packets belong to the one tracked
revision, their receipt digests match, the `free` model and completed
research journey were observed each time, the 72-hour and seven-day gates
elapsed, all three checkpoint numbers are present, and the packet total proves
at least 30 successful operations. A failing report is retained evidence for a
revise or reject decision; it must not be rewritten into a passing state.

## End isolated fixture verification

After the final checkpoint, or when abandoning the isolated sequence, change
`FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS` to `false` in the same private
`$CANDIDATE_ENV`. Then apply that setting only to the demo agent:

```sh
unset FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" \
  up -d --no-deps --force-recreate candidate-agent
```

Keep the dedicated file false afterward so any later normal Compose recreation
also disables new fictional runs. Retained artifacts and workspace reads keep
their existing feature gates. Do not enable fixture admission in production.

## Stop without destroying evidence

```sh
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" stop
```

For another fixture restart check, repeat the isolated opt-in and agent
recreation above before using `start` and the verifier, and reset the dedicated
file after that sequence. `down` removes containers and networks while
preserving named volumes. Do not use `down -v`;
volume removal belongs only to the later, separately reviewed retirement step.

## Failure boundary

If candidate readiness or verification fails, stop admitting experimental work,
capture `docker compose ps` and service logs, and leave the incumbent deployment
unchanged. During the pgvector rehearsal, switching the candidate semantic mode
back to Qdrant is the rollback operation. Deleting PostgreSQL or Qdrant data is
never part of rollback.

## Completed W9 window

The September 23–30 `free` window passed all three checkpoints and 36 declared
operations. The final suite passed its single allowed retry after a model
readiness timeout. See the [final recommendation](../experiments/w9-replacement-readiness-decision.md).
Magnus ratified the bounded ADRs on September 30. Keep rollback available; documentation
closeout does not require a redeployment. Future runtime or material configuration
changes require fresh evidence rather than extending this completed window.
