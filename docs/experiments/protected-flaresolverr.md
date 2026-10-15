# Protected FlareSolverr profile

This candidate-only profile preserves FlareSolverr's `/`, `/health`, and `/v1`
API, including request GET/POST, sessions, cookies, and the upstream Selenium
challenge logic. The candidate scraper points its existing FlareSolverr client
at the controller bridge only when the candidate Compose overlay is selected.
The ordinary Compose stack is unchanged.

## Frozen upstream input

The profile starts from FlareSolverr v3.5.2, the latest upstream release checked
for this implementation. The upstream release page identifies signed release
commit `93125d6`; the official GHCR image is pinned as
`ghcr.io/flaresolverr/flaresolverr:v3.5.2@sha256:c80ae007ce2ccdcd217a12426e4f039ef763ff90738c808d38810c3e59323767`.
The image contains Chromium and `/app/chromedriver`; upstream `utils.get_webdriver`
selects that bundled driver in Docker instead of downloading another driver.
The Docker build applies narrow source substitutions and fails if the expected
upstream source statements have changed. It adds no replacement browser or
challenge implementation.

Upstream accepts proxy configuration per request and session. The protected
patch overwrites that field at API ingress with the fixed candidate gateway,
validates request URLs to HTTP or HTTPS, and binds Waitress to a Unix socket.
The matching controller bridge on the candidate private network forwards only
the existing API paths to that socket. It allows two active requests, rejects
excess clients with HTTP 503, applies a 15-second client socket timeout, and
caps responses at 8 MiB with a 256 MiB container memory limit. The Flare/Chromium container is not on
the controller network and has no TCP Flare API listener. The page fixture
attempts to fetch the API over loopback; the profile smoke test expects that
attempt to fail.

The browser receives the numeric gateway proxy, an explicit Chromium
`<-loopback>` proxy-bypass override, and disabled browser background update
traffic. The container firewall allows established traffic, loopback (needed
by the local Selenium/ChromeDriver control plane), and TCP to the gateway's
fixed internal address and port. It denies other direct IPv4 and IPv6 traffic.
Therefore this profile claims source egress is gateway-bound; it does not claim
that every loopback socket is unreachable from browser internals. The Flare API
itself is absent from those loopback TCP sockets and is reachable only through
the separate controller bridge and shared Unix socket.

## Local fixture qualification

The Runtime CI profile adds a synthetic origin on the public test address
`93.184.216.34` inside a Docker network marked `internal`; the gateway resolves
`flare-origin.test` to that address from its container hosts file. The network
has no route to external destinations. The origin exercises page load,
redirects, cookie return, screenshot capture, POST submission, and an in-page
attempt to reach the Flare API. The same test sends a caller-supplied proxy
pointing at the absent loopback API; successful fixture capture demonstrates
that the caller proxy did not replace the configured gateway. The control API
also verifies sessions create/list/destroy and rejects `file:` URLs.

To render the opt-in candidate profile, set the required candidate environment
values as described by `compose.experimental-candidate.yml`, then run:

```sh
docker compose --project-name candidate-flare \
  --env-file /path/to/candidate.env \
  -f compose.experimental-candidate.yml \
  --profile protected-flare \
  config --quiet

docker compose --project-name candidate-flare \
  --env-file /path/to/candidate.env \
  -f compose.experimental-candidate.yml \
  --profile protected-flare \
  up --build --detach --wait candidate-flare-control
```

The CI-only fixture overlay is intentionally omitted from these operator
commands. Do not publish or expose the Flare API or controller bridge outside
the candidate private network.

For a private OpenAI-compatible endpoint, `LLM_GATEWAY_PRIVATE_HOSTS` must
list its exact DNS hostname. A separate model-only egress gateway accepts only
the configured model authorities and pins a numeric peer. The model broker
has no direct network egress; it sends requests through that gateway, while
the scraper receives a fixed UDS route. Source capture still uses a separate
global-address-only gateway.

## Scraper composition status

The candidate scraper now selects the capture gateway for HTTPX, curl-cffi,
and the migrated adapter clients, and its protected Tier 3 path delegates to
the isolated browser renderer rather than launching page JavaScript inside the
scraper. FlareSolverr and browser-service calls remain separate trusted
control requests. The scraper is no longer attached to `candidate_egress`.

The candidate composition now places the scraper only on the internal capture
link. A namespace firewall defaults IPv4 and IPv6 input/output/forwarding to
deny before the process drops to UID 10001/GID 20000 and clears its bootstrap
capabilities. Only DNS to Docker's local resolver and TCP to the fixed
capture-gateway peer are allowed. A capability-free Tini process then runs as
PID 1, forwards shutdown signals, and reaps renderer children. The profile
checks both PID 1 and the scraper process identity/capabilities, plus the
group-20000 socket mode. It probes the host bridge, a second
peer on the same capture network, another private bridge, and the public
network from inside the unprivileged scraper process.

Its agent-facing HTTP ingress, Valkey operations, browser and Flare
controls, and model calls use separate fixed Unix-socket capabilities. The
model broker owns configured credentials and fixed destinations. It has no
direct egress: a separate model-only gateway accepts only the exact configured
model authorities, resolves once, and dials the pinned numeric peer. Private
addresses are allowed only for an exact hostname named in the operator's
`LLM_GATEWAY_PRIVATE_HOSTS` grant. The source-capture gateway policy remains
global-address-only. The CI composition adds a
private, same-capture, and host-bridge sentinels; exercises successful
`/scrape`, forced-browser `/scrape`, and `/scrape/meta` through the ingress;
checks typed Valkey cache/robots/cookie/rate operations and reservation Lua;
and tests that hostile browser JavaScript cannot reach the private sentinel.
The Valkey capability accepts at most two active frames, limits stored values
to 8 MiB, and bounds serialized frames to the sixfold JSON-escaping maximum.

This composition remains **unqualified** until the combined Docker Runtime
Gate passes. Unit tests and rendered Compose configuration do not prove the
container network, socket permissions, or runtime request paths. Do not enable
it as a production security boundary before that job succeeds and its boundary
probes are reviewed.

The Flare profile itself remains an opt-in integration candidate, not a
deployment or full capture-stack qualification. The tests do not exercise real
Cloudflare/DDoS-GUARD challenges, external targets, every browser side channel,
or production workloads. Keep protected operation unavailable until every
component and negative boundary check passes. The public FlareSolverr version
and image identity can change upstream; review and freeze a new source/image
pair before updating the base pin.

## Primary sources

- [FlareSolverr v3.5.2 release](https://github.com/FlareSolverr/FlareSolverr/releases/tag/v3.5.2)
- [FlareSolverr v3.5.2 API server](https://github.com/FlareSolverr/FlareSolverr/blob/v3.5.2/src/flaresolverr.py)
- [FlareSolverr v3.5.2 challenge controller](https://github.com/FlareSolverr/FlareSolverr/blob/v3.5.2/src/flaresolverr_service.py)
- [FlareSolverr v3.5.2 Chrome and proxy setup](https://github.com/FlareSolverr/FlareSolverr/blob/v3.5.2/src/utils.py)
- [Official FlareSolverr GHCR package](https://github.com/FlareSolverr/FlareSolverr/pkgs/container/flaresolverr)
- [Chromium proxy settings](https://www.chromium.org/developers/design-documents/network-settings/)
