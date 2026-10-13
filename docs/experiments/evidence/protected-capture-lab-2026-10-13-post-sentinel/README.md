# Protected capture lab readiness packet

**Status: deployment, boundary, TLS, and one public capture readiness check
passed once.** This is lab readiness evidence only; research quality, study
admission, and internal model fanout remain unqualified. The deployment
postcondition and preservation checks were independently audited, and the six
boundary probes, two positive controls, ten TLS/authentication checks, and one
public capture passed once. Preserve failed attempts and historical evidence;
do not replace or rewrite the existing
[2026-10-13 qualification packet](../protected-capture-lab-2026-10-13/README.md).

## Source and procedure

- Merged source revision: `ab8ffb5efec63df6c2f329b12b28eb80c316c434`
- Refreshed rendered configuration SHA-256: `b9026f78a3bca28112b6d65d9c32a168c786fab5c9582d0c5567cf3b82502099`
- [Sanitized deployment and boundary result](result.json)
- [Protected capture lab qualification procedure](../../../guides/protected-capture-lab-qualification.md)
- [Prior sanitized transport and boundary packet](../protected-capture-lab-2026-10-13/README.md)
- [Host bridge target validator and positive-control probe](../../../../tests/integration/host_bridge_sentinel.py)
- [Shared sentinel ledger verifier](../../../../tests/integration/sentinel_ledger.py)
- [Scraper ingress boundary probe](../../../../tests/integration/scraper_ingress_boundary_probe.py)
- [Scraper egress boundary probe](../../../../tests/integration/scraper_egress_boundary_probe.py)
- [Protected Flare API probe](../../../../tests/integration/protected_flare_api_probe.py)
- [Browser capture boundary probe](../../../../tests/integration/browser_capture_boundary_probe.py)
- [Browser renderer isolation probe](../../../../tests/integration/browser_renderer_isolation_probe.py)
- [Protected Flare renderer probe](../../../../tests/integration/protected_flare_renderer_probe.py)

## Evidence status

| Evidence | Status | Sanitized receipt reference |
| --- | --- | --- |
| Targeted deployment and health postconditions | Passed | `cd80b00022cbc4fdfb5da035dabbcf0b90038f629e307b01d268a03f0efb39b6` |
| Preserved non-target service, image, mount, network, and host snapshots | Passed | `cd80b00022cbc4fdfb5da035dabbcf0b90038f629e307b01d268a03f0efb39b6` |
| Append-only private and host positive-control checks | Passed | `d7445c106d17093aed6107863da8bf05eedd820d56b8dbf629743258cb9797a0` |
| Six protected boundary probes | Passed | `d7445c106d17093aed6107863da8bf05eedd820d56b8dbf629743258cb9797a0` |
| Authenticated TLS-verified public capture readiness | Passed once | `ed33d459f781585e9b2c921a1a84a1efde0b4c90e14ce83cac00ca3f2bafe0e2` |
| Refreshed rendered configuration and source identity | Passed | `d7445c106d17093aed6107863da8bf05eedd820d56b8dbf629743258cb9797a0` |

The independent deployment audit verified five targeted services healthy,
twenty non-target services unchanged, all nine network identities and
addresses preserved, and image, environment, and security settings preserved.
It accepted only documented Compose metadata equivalences: working-directory
and dependency metadata, empty DNS option/search lists represented as `[]`
versus `null`, and relocation of a read-only ingress probe whose old, new, and
merged bytes matched exactly. The initial apply guard failure and three
read-only auditor refusals remain retained evidence; no repeated deployment
was used to clear them.

The profile SHA-256 is `38176b5e36dfbd4af3e441a68f959dc460ad6b313c3b4729726da3684749dce0`; the operator qualification receipt SHA-256 is `10b0286bb82fd18ee024c0e9e16333588a64f2590f97b833000a0cccb6e34f62`. The fresh boundary qualification had zero retries. The scraper ingress probe
passed in 3.171 seconds, scraper egress in 9.580 seconds, protected Flare API
in 6.575 seconds, browser controller in 2.342 seconds, browser renderer in
0.139 seconds, and Flare renderer in 0.102 seconds. Every probe exited zero
with empty stderr. The egress probe included one bounded eight-token
engineering transport request; it carries no scientific or research-quality
credit.

The host positive control passed once in 0.218 seconds and the private
positive control passed once in 1.316 seconds. The append-only host ledger
advanced from zero to one positive record, the private ledger from two to
three, and the negative ledger remained at zero. No ledger was reset or
retried. Their sanitized receipt is the boundary receipt referenced above. The complete boundary receipt SHA-256 is `d7445c106d17093aed6107863da8bf05eedd820d56b8dbf629743258cb9797a0`.

TLS and authentication readiness passed once: all ten checks passed, covering missing and invalid credentials, route/method/query restrictions, duplicate authorization, and rejection of an untrusted CA and incorrect certificate hostname. A public hostname was accepted by asynchronous policy while a private literal was denied. One authenticated public capture returned HTTP 200, contained the expected marker, and yielded 2,207 Markdown characters (2,654 response bytes). No search calls or retries occurred. Model or internal fanout during the capture was not assessed. The sanitized TLS/readiness receipt is `ed33d459f781585e9b2c921a1a84a1efde0b4c90e14ce83cac00ca3f2bafe0e2`.

One earlier preflight failed before probes, model calls, or controls because a
short network identifier was used where the full identifier was required. The
helper was corrected to use the full identifier, and the fresh run above
completed. The earlier preflight remains retained as setup history and is not
counted as qualification evidence.

Keep exact probe outcomes, durations, nonce-bound ledger deltas, and hashes only
when supported by the supplied sanitized receipts. Keep configuration values,
operator paths, host details, credentials, raw logs, and raw responses private.

## Scope

Boundary, TLS, and one public capture readiness behavior are supported by the recorded receipts for the exact source and configuration above. These checks establish lab readiness only. Research quality, study admission, and internal model fanout remain unqualified. The earlier setup failure remains part of the record. This packet does not authorize another deployment, external provider request, or study admission, and it makes no research-quality claim.
