# Protected capture lab qualification

The [2026-10-13 live lab packet](../experiments/evidence/protected-capture-lab-2026-10-13/README.md) records ten passing TLS/authentication checks, six passing capture-boundary probes, a verified public capture, and one bounded `free` model transport check. It qualifies only the recorded API profile and configuration; the candidate MCP path was not instantiated. It does not admit a research study or establish research quality. The [2026-10-11 packet](../experiments/evidence/protected-capture-lab-2026-10-11/README.md) remains as historical evidence. Qualify any materially different runtime separately.

`compose.protected-capture-lab.yml` adds deterministic, internal-only origin
and sentinel fixtures to the candidate profile. It does not replace the
configured LLM service or its route. The overlay intentionally contains no
`candidate-llm` service and does not change `candidate-model-egress` or
`candidate_model_upstream`.

Use the same ordered Compose files, project name, env file, fixture hostname,
and image set for configuration validation, startup, probes, and retained
receipts. The profile declares its egress network as `172.31.252.0/24`, separate
from the private `172.31.253.0/24` and fixed `172.31.254.x` capture networks;
none should be left for Docker's automatic address pool. Check these ranges
against host routes and existing Docker networks before starting the project.
The origin subnet is
internal to this project; the `capture-origin.example.test` name is a fixture
name, not a real destination. `CAPTURE_HOST_BRIDGE_IP` selects the explicit
gateway in the candidate capture bridge IPAM configuration and is reused for
the host sentinel bind, healthcheck, and scraper denial target. Its generic
default is the fixture gateway. `CAPTURE_HOST_BRIDGE_SUBNET`,
`CAPTURE_HOST_BRIDGE_IP_RANGE`, `CAPTURE_EGRESS_IP`, and `CAPTURE_PEER_IP` form
the capture bridge address plan. The same values are rendered into IPAM, fixed
service assignments, firewall gateway configuration, worker denial probes,
and host sentinel validation. The gateway must be an unused private IPv4 in
the subnet and distinct from the configured proxy and peer addresses.
The listener binds only that gateway on
its two probe ports; the overlay publishes no sentinel ports.

```bash
compose=(docker compose \
  --project-name "$CAPTURE_LAB_PROJECT" \
  --env-file "$CANDIDATE_ENV_FILE" \
  -f compose.experimental-candidate.yml \
  -f compose.protected-capture-lab.yml \
  -f compose.capture-https.yml \
  --profile protected-flare \
  --profile protected-capture)

"${compose[@]}" config --quiet
```

The private env file supplies the candidate's already-authorized model URL,
model alias, and API key, plus PostgreSQL/API secrets and HTTPS certificate,
private-key, and bearer-file paths. The client-side CA file is supplied
separately to the HTTPS probe. Do not print or copy these private values into
public artifacts. The overlay exposes only the non-secret `LLM_MODEL` alias to the probe as
`CAPTURE_PROBE_MODEL_NAME`. `CAPTURE_FIXTURE_HOST` may be set in that private
env file; its default is `capture-origin.example.test`. Changing it changes
the measured Compose configuration and requires a new config/profile receipt.
For an operator IPAM override, set the subnet, gateway, dynamic range, egress
address, and peer address together in the private environment. Verify the
rendered network and all sentinel references agree before recreation. The
listener rejects missing, non-private, out-of-subnet, and reserved proxy or
peer addresses. Do not change its bind independently.

After separately authorizing a lab run, start the complete protected profile
from the prepared image set:

```bash
"${compose[@]}" up --no-build --pull never --detach --wait \
  candidate-capture-https candidate-scraper candidate-scraper-ingress \
  candidate-flare-control candidate-flare-test-origin \
  candidate-private-sentinel candidate-capture-peer-sentinel \
  candidate-host-bridge-sentinel
```

Before any negative egress result can count, prove the host listener is reachable
from the trusted capture-gateway peer. This sends one nonce-bound request and
records it in a separate host-bridge positive-control ledger:

```bash
"${compose[@]}" exec -T candidate-capture-egress python /probe/host_bridge_sentinel.py
```

The probe validates the exact nonce response and prints that nonce. An
unhealthy host sentinel, timeout, unexpected response, or mismatched configured
target stops qualification before the negative probes. Keep the host positive
ledger's existing prefix and require exactly one new event for this run whose
nonce matches the probe output; never clear it or retry the positive request.

Run the existing ingress, egress, and Flare probes against that same project.
Their fixture URL and model alias come from the overlay while CI continues to
use the existing `flare-origin.test` and `fixture-model` defaults.

```bash
"${compose[@]}" exec -T candidate-private-sentinel python - \
  < tests/integration/scraper_ingress_boundary_probe.py
"${compose[@]}" exec -T --user 10001:20000 candidate-scraper python - \
  < tests/integration/scraper_egress_boundary_probe.py
"${compose[@]}" exec -T --user 10001:20000 candidate-scraper python - \
  < tests/integration/protected_flare_api_probe.py
"${compose[@]}" exec -T --user 10002:20000 candidate-browser-controller python - \
  < tests/integration/browser_capture_boundary_probe.py
"${compose[@]}" exec -T candidate-browser-renderer python - \
  < tests/integration/browser_renderer_isolation_probe.py
"${compose[@]}" exec -T candidate-flare-renderer python - \
  < tests/integration/protected_flare_renderer_probe.py
```

The egress probe makes one bounded completion request through the configured
model broker to verify that the real configured model path is reachable. It
must be included in the lab's authorized call budget. It requests at most eight
output tokens and uses a 155-second total deadline, covering the existing
150-second model-proxy connection bound plus local broker overhead. This is a
transport diagnostic; the research study's answer deadlines and quality gates
are unchanged. The other listed probes
exercise ingress, source capture, browser, Flare, and sentinel behavior. The
CI composition still uses its separate fixture-model overlay and is not a
substitute for a lab run. A passing configuration or synthetic test does not
qualify a deployed lab profile.

Before capture checks, confirm the configured source-gateway resolver is ready
to resolve public hostnames. The scraper's isolated resolver is not expected to
resolve public names: its pre-adapter guard asks the source gateway's bounded
DNS-policy endpoint, then the actual request is independently resolved and
connected through that same gateway. Run a representative public-URL capture
through the complete scraper path after resolver readiness. API health and a
successful DNS-policy preflight alone do not establish capture readiness; the
check must include the actual proxied request. After any IPAM change, recreate
the affected network and containers before repeating these checks.

After the six probes, generate the private sentinel positive control once.
Do not repeat it or clear retained sentinel files when resuming a run:

```bash
"${compose[@]}" exec -T candidate-private-sentinel python -c 'import os, uuid; from urllib.request import urlopen; nonce=uuid.uuid4().hex; host=os.environ["SENTINEL_BIND_HOST"]; r=urlopen(f"http://{host}:19001/positive-control/{nonce}", timeout=2); body=r.read(128); assert r.status == 200 and r.headers.get("Content-Type") == "text/plain" and r.headers.get("Access-Control-Allow-Origin") == "*" and body == f"PRIVATE_SENTINEL_RESPONSE_V1:{nonce}".encode(); print(nonce)'
```

After probes finish, inspect the append-only sentinel files in the private
project volume: `events.jsonl` must remain empty; `positive-control.jsonl` must
retain its historical prefix plus the one private-sentinel control; and
`host-bridge-positive-control.jsonl` must retain its historical prefix plus
exactly one host-bridge control. Each new positive entry contains the accepted
32-character request nonce, and each probe validates the response against that
same nonce. Retain the line counts and hashes with the
resolved configuration hash and exact image inventory. Never clear sentinel
events to make a later run appear clean.
