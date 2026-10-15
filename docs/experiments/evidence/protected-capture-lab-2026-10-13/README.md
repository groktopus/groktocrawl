# Protected capture transport qualification

The isolated API lab passed the six protected boundary probes and one bounded
transport request through the configured model route. An authenticated,
TLS-verified public IANA capture returned HTTP 200, included the expected
marker, and produced 2,207 Markdown characters in a 2,654-byte response. The
private-literal guard denied its test destination while the public-hostname
policy accepted the capture destination.

All ten TLS/authentication surface checks passed: missing and invalid bearer
credentials returned 401; authenticated health returned 200; wrong method,
health-query, and unlisted-route checks returned 404; duplicate authorization
headers returned 400; and untrusted-CA and wrong-hostname certificates were
rejected during certificate validation. These checks made zero scrape, model,
or search calls. Their private receipt is bound by the hash in
[result.json](result.json).

The exact tested configuration is identified by the sanitized rendered
Compose SHA-256 in [result.json](result.json). The final profile had 26
configured services, of which 25 were deployed: 23 running services and two
completed initialization services. For the final fixture-alias removal step,
the candidate scraper was the only service recreated; the other 24 deployed
service snapshots remained unchanged, including their provider environment,
image references, mounts, networks, and host settings. The API path is
qualified for this recorded profile. The candidate MCP path was not
instantiated and is not qualified.

The six probes all exited successfully without retries: ingress (1.673 s),
egress (6.563 s), Flare API (5.637 s), browser controller (2.112 s), browser
renderer (0.085 s), and Flare renderer (0.075 s). The bounded model transport
probe made one request with an eight-token output cap and passed. This is a
connectivity check, not a research-quality result; upstream usage, internal
model fanout, answer quality, and study admission are not assessed.

The first readiness helper refused the existing publication before making a
request. A corrected check connected only through loopback and matched the
existing rendered and runtime publication; no listener or deployment setting
changed. The earlier helper outcome remains a setup failure, not a qualification
result.

The sentinel retained its historical positive-control record, appended exactly
one fresh positive control (one record before, two after), and recorded zero
forbidden requests. Its private receipt hash is recorded in
[result.json](result.json). Historical qualification failures and helper/setup
failures remain preserved as failed
or non-evidentiary attempts; they receive no quality credit and were not
repaired or regraded as scientific results. The preserved record includes the
earlier fixture-DNS boundary failure, a strict-upgrade postcheck failure,
runtime-image patch-version drift and bind-order discrepancy, and helper
environment/readiness setup failures. These are engineering evidence only;
they do not count as successful qualification or research evaluations.

This packet is limited to the exact API-only profile and configuration hash
recorded here. It does not qualify candidate MCP, establish research quality,
authorize a new study, recommend replacing mainline, or assess downstream
model fanout. No Brave, Jev, answerer, or native-grader calls were made.

The public receipt contains no private hostnames, IP addresses, credentials,
operator paths, raw configuration, raw logs, or raw responses. The existing
[operator procedure](../../../guides/protected-capture-lab-qualification.md)
continues to require requalification after material image or configuration
changes.

- [Sanitized result](result.json)
- [Issue #450](https://github.com/magnus919/groktocrawl-x/issues/450)

Result SHA-256: `96cd2d80bff1158172ff385b0413414fff2d4a4a05715d827431ea2f0718080a`.

