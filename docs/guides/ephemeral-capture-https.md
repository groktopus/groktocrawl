# Ephemeral HTTPS ingress for research capture

`compose.capture-https.yml` is an opt-in inbound HTTPS edge for the experimental
candidate API. It is intended for a short, explicitly authorized research
window. It does not change source-egress policy or qualify the protected capture
profile.

Compose the overlay after `compose.experimental-candidate.yml`, using the same
private candidate environment file and exact candidate image revisions as the
protected-browser and protected-Flare setup. The overlay replaces the
candidate agent's and MCP service's plaintext host publications with one TLS
listener. MCP remains available only on the private Compose network.
The listener is on the private Compose network and allows only bearer-authenticated
`GET /health` and `POST /v2/scrape`; all other paths and methods are denied.
It forwards the bearer header to the agent, which continues to enforce its API
key for `/v2/scrape`. Health is also authenticated at the edge even though the
agent's own health endpoint is public on the internal network.

Before an operator starts the stack, configure these private values outside the
repository:

- `CAPTURE_TLS_CERT_FILE`: absolute path to the server certificate chain.
- `CAPTURE_TLS_KEY_FILE`: absolute path to its private key.
- `CAPTURE_BEARER_ACL_FILE`: absolute path to a one-line file containing
  `Bearer <fresh-api-key>`.
- `CAPTURE_HTTPS_UID` and `CAPTURE_HTTPS_GID`: positive numeric identity for
  the non-root frontend process and its temporary filesystem (defaults to
  `1000`). Set both to the operator account that owns the mounted secret files.
- `CANDIDATE_API_KEY`: the same fresh key without the `Bearer ` prefix, as
  required by the existing agent and MCP service configuration.
- `CAPTURE_HTTPS_BIND_IP` and `CAPTURE_HTTPS_HOST_PORT`: the host interface and
  port for the TLS listener. The default binds only to loopback. Use a LAN
  interface only when the lab requires it and the firewall is appropriately
  scoped.

The frontend rejects startup if it is running as UID 0 or cannot read its
mounted files. Ensure the certificate chain, private key, and bearer ACL file
are owned by the selected numeric identity and readable only by that identity
(for example, mode `0600`). The temporary combined certificate file is created
in an identity-matched `0700` tmpfs. The mounted source files remain read-only.

Keep the certificate hostname or IP address in the certificate SAN equal to
the URL used by the capture client. An operator-controlled LAN name or IP and
private CA are supported; public DNS is not required. The client must trust the
issuing CA and verify the certificate name. The TLS private key is mounted
read-only and only the frontend can read it; the frontend combines it with the
certificate chain in a private temporary filesystem at startup. No client
certificate is required. The bearer key is the application authorization
layer.

The frontend has no access or request logging configuration, does not rewrite
the bearer header, and retries no requests. It is attached to
`candidate_private` for the agent route and to a dedicated non-internal bridge,
`candidate_https_edge` (`172.31.251.0/24`), because Docker does not publish host
ports for a container attached only to an internal network. Check that this
subnet does not overlap host routes or another Docker network before starting
the lab. This bridge allows outbound routing; it does not add an egress block.
Only the HTTPS frontend joins it. The source capture gateway, isolated scraper
worker, browser renderer/controller, and Flare recovery path remain as
configured by the selected protected-profile files. Do not use this ingress
overlay by itself as evidence that those source paths are protected.

The Compose model and unit contracts can be checked without starting services:

```sh
docker compose --project-name capture-check \
  --env-file /path/to/private-candidate.env \
  -f compose.experimental-candidate.yml \
  -f compose.capture-https.yml config --quiet
pytest tests/unit/test_capture_https_ingress.py -q -o addopts=
```

After bringing up an authorized ephemeral lab, use an HTTPS client configured
with the operator's CA bundle and verify the certificate hostname. Check that
the fresh bearer succeeds for both allowed routes, missing/incorrect bearers
are rejected, and every other path or method is denied. Then run the complete
ADR-0099 boundary probes against the exact resolved image digests and full
recovery profile. A rendered Compose file, unit test, healthy endpoint, or this
frontend alone is not protected-profile qualification or study admission.

Component-only verification on 2026-10-09 passed the HAProxy parser and a
loopback TLS integration run under a non-root identity with networking disabled.
It exercised authenticated health and scrape routes, rejected wrong CA and
hostname checks, and denied wrong paths, methods, and queries. The first strict
TLS run exposed a missing Authority Key Identifier in the test CA; the fixture
was corrected with the required CA and leaf extensions, with certificate
verification left enabled. This did not exercise Compose frontend startup or
qualify the complete protected capture profile.

A subsequent isolated startup smoke on 2026-10-09 used the overlay's actual
digest-pinned HAProxy image and startup command with UID/GID 1000, all
capabilities dropped, a read-only root filesystem, an identity-owned temporary
filesystem, and separate read-only certificate, key, and bearer files. With no
network or host-published port, it verified TLS, bearer enforcement, exact
route/method/query handling, and rejection of the wrong CA and hostname. The
authorized health and scrape routes returned 503 because no backend was
attached; this did not verify agent health or a successful scrape. The first
setup attempt stopped before generating test material because the image
prerequisite was not locally available; the pinned image was made available
before the successful run, without weakening certificate verification. This
smoke does not verify Compose startup or qualify the protected profile.
