# Testing and coverage policy

GroktoCrawl uses two different coverage signals. The existing aggregate pytest-cov
floor is a broad smoke check. The changed-line gate is the risk signal for source
code that is being changed now.

## CI lanes and scope

| Lane | Tests | Measured source | Exclusions and limits |
| --- | --- | --- | --- |
| Fast Tests | `tests/unit/` and `tests/service/` | `agent-svc/agent`, `scraper-svc/scraper`, `browser-svc/browser_svc`, `parse-svc/parse_svc`, `portal-svc/portal`, `semantic-svc`, and `common` | External tests, fixture-only `llm-svc`, `mcp-svc`, `test-site`, and generated/build files are outside this lane. |
| Docker Integration | `tests/integration/` and `tests/service/`, plus the critical-journey smoke | The service packages copied into the `agent-svc` test container and `common` | The runtime image does not contain `semantic-svc`; its coverage is owned by the Fast Tests lane. Live third-party probes remain excluded unless explicitly opted in. |

Fast Tests uses the locked `fast-tests` dependency group. Docker Integration installs
`pytest-cov` in the test container before each pytest invocation. Both lanes write
JSON/XML coverage artifacts and a changed-line summary. The summary identifies the
changed modules even when no high-risk source file was modified.
The gate compares against the checkout's `github.sha`, so pull-request coverage
coordinates with the merge tree that pytest actually measured rather than the
source-branch tip.
If Git cannot resolve the requested base revision, the gate fails closed with an
explicit error instead of producing a partial changed-line result.

The other CI jobs are not coverage-enforcing jobs: Code Quality checks static
properties, CLI Coverage checks API/CLI parity, and dependency/security jobs inspect
those respective contracts. They do not install or invoke pytest and therefore do
not claim a coverage result.

## Changed-line policy

`scripts/coverage_gate.py` computes added and modified destination lines from the
exact base/head diff. It then intersects those lines with executable lines reported
by coverage.py. Comments, blank lines, tests, documentation, workflows, and other
non-source changes do not create coverage obligations. A lane that did not import a changed
module has no coverage.py entry for it; the gate reports that module as informational
rather than treating every changed line as uncovered. Coverage ownership remains with
the lane that measures the module, so a missing report does not bypass the complete
Fast Tests policy.

When a workflow event creates a ref with no prior commit, GitHub supplies an all-zero
base SHA. The workflow skips only the changed-line comparison in that case because no
meaningful diff base exists; the test lane and its aggregate coverage still run.

- The existing aggregate floor remains **20%**. It is not a target and is not raised
  by this policy.
- A standard source module has an **80% changed-line target**. Results below that
  target are informational while service baselines and test effectiveness mature.
- A high-risk module has a **90% changed-line enforcement floor**. A changed high-risk
  module below that floor fails the CI coverage gate.
- A high-risk exception must be recorded in `qa/coverage-exceptions.toml` with an
  issue reference, reviewer, ISO expiry date, reason, and `reviewed = true`. Expired,
  incomplete, or unreviewed entries do not bypass the gate.

The initial high-risk set starts with outbound URL and SSRF policy:

- `common/url.py`
- `scraper-svc/scraper/fetch.py`
- `scraper-svc/scraper/fetch_tiers.py`
- `browser-svc/browser_svc/app.py`
- `agent-svc/agent/crawler.py`
- `agent-svc/agent/webhook.py`
- `agent-svc/agent/monitor.py`

These modules can turn user-controlled destinations into network requests or enforce
private-network boundaries. The list is intentionally explicit and should grow only
with a reviewed policy change.

## Baseline and threshold decision

A valid baseline was measured in the locked Fast Tests environment after adding the
`common` scope, with 1,156 passing unit/service tests:

| Scope | Statements | Covered | Coverage |
| --- | ---: | ---: | ---: |
| `agent-svc` | 7,048 | 3,118 | 44.24% |
| `scraper-svc` | 5,810 | 2,211 | 38.06% |
| `browser-svc` | 259 | 154 | 59.46% |
| `parse-svc` | 211 | 157 | 74.41% |
| `portal-svc` | 87 | 57 | 65.52% |
| `semantic-svc` | 771 | 399 | 51.75% |
| `common` | 483 | 449 | 92.96% |
| **Aggregate** | **14,669** | **6,545** | **44.62%** |

The aggregate 20% floor remains unchanged. Raising it without mutation or other
fault-detection evidence would create a number without demonstrating better tests.
The changed-line floor is a narrower, reviewable control and does not claim that the
current service baselines are complete.

## Mutation-testing decision

Mutation testing or an equivalent fault-detection check is warranted for the initial
high-risk set, beginning with `common/url.py` and the URL/SSRF call paths listed above.
The first useful mutation campaign should demonstrate that tests detect changes to:

- private, loopback, link-local, metadata, and IPv4-mapped IPv6 rejection;
- exact-host allowlist behavior;
- DNS-rebinding and transient-resolution handling; and
- redirect or outbound-webhook destination validation.

This issue records the warranted scope and test expectations. It does not add an
unbounded mutation job to every pull request. A follow-up should run a bounded,
reproducible campaign on these policy-critical modules, publish survivors, and use
that evidence before changing the aggregate floor.
