# Capture egress gateway component

This experimental component accepts HTTP CONNECT and absolute-form HTTP requests through the shared DNS/socket destination policy. It preserves TLS end to end, limits concurrency and transfer size, and bounds connection lifetime and cleanup. The separate loopback health handler reports process liveness only.

The opt-in `compose.capture-https.yml` overlay adds a separate inbound TLS
frontend for the experimental agent API. That frontend does not use this
gateway. The opt-in protected candidate composition routes scraper, browser,
and FlareSolverr source traffic through the gateway. Neither the frontend nor
component tests qualify a deployed profile or authorize a live study. Exact
image and configuration qualification remains required by issue #429 and
proposed ADR-0099.

The source gateway also accepts a bounded, hostname-only DNS-policy preflight
on its existing listener. Protected scraper workers use it when their isolated
namespace cannot resolve public names. It returns only public, denied, or
unavailable, never addresses, and opens no origin connection. Every actual
proxy request independently resolves and validates the destination before
numeric dialing and peer verification. The model gateway does not expose this
control route. Resolver and transport outages fail closed with
`SOURCE_DESTINATION_VALIDATION_UNAVAILABLE`; a denied source retains
`PRIVATE_URL_BLOCKED`.

Component validation uses local loopback fixtures and fake stalled writers; no external search or provider requests are required. The current gateway suite has 26 passing tests, including opaque tunnels, HTTP bodies, destination denial, request framing, byte/concurrency limits, cancellation and bounded cleanup. These tests prove their declared component scenarios, not production isolation.

Runtime CI also builds this Dockerfile and starts the image with `--network none`. Its probe checks the real entrypoint's loopback liveness endpoint and private-origin refusals. This image smoke test makes no origin requests and does not establish the future protected capture network's isolation.
