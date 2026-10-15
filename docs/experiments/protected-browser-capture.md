# Protected browser capture profile

The experimental candidate Compose overlay has an opt-in browser profile that
keeps the existing browser API while separating its network-facing controller
from the Chromium renderer. The controller forwards the current API over a
fixed Unix socket, and the renderer's only permitted network destination is
the capture gateway. Clearance-cookie values remain in Valkey; the renderer
uses a narrow Unix-socket RPC for the existing domain-scoped, expiring cookie
operations. The internal capture network uses a fixed private subnet with
reserved gateway and renderer addresses so the renderer firewall can allow
only the gateway's numeric address.

The ordinary `docker-compose.yml` profile is unchanged. This profile is selected
only by explicitly naming `compose.experimental-candidate.yml`; it is not
enabled by the default Compose file. For example, using the private candidate
environment file already required by that overlay:

```sh
docker compose --project-name candidate-browser \
  --env-file /path/to/candidate.env \
  -f compose.experimental-candidate.yml config --quiet

docker compose --project-name candidate-browser \
  --env-file /path/to/candidate.env \
  -f compose.experimental-candidate.yml \
  up --build --detach --wait candidate-browser-controller

docker compose --project-name candidate-browser \
  --env-file /path/to/candidate.env \
  -f compose.experimental-candidate.yml down --volumes --remove-orphans
```

The candidate environment file must provide the overlay's required settings
and secret-file path. If the configured OpenAI-compatible endpoint resolves to
a private address, `LLM_GATEWAY_PRIVATE_HOSTS` must grant its exact DNS host.
The model broker has no direct network egress; a separate model-only gateway
accepts only configured model authorities, resolves once, and dials a vetted
numeric peer. Keep the file private and do not place credentials in commands,
logs, or this document.

This is an opt-in integration and qualification step, not full capture-stack
qualification. The offline checks cover the controller/renderer socket
boundary, cookie RPC contract, exact default-deny firewall rules, and synthetic
gateway behavior. The hosted runtime job builds the selected Compose dependency
graph and probes Chromium's local-network boundary and renderer privileges.
Passing these checks does not establish qualification against all sites,
redirect patterns, browser side channels, or production workloads. The profile
has not been deployed, and the default mainline stack remains unchanged.
