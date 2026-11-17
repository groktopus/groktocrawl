# Preserve Provider-Aware Barrier Diagnostics Before Extraction Gates

- Status: accepted
- Date: 2026-11-17

## Context

A Food Lion scrape reached a DataDome HTTP 403 iframe with no body text.
The production pipeline reduced this to an unknown barrier and reported
FlareSolverr and browser-svc as unavailable despite successful service calls.
The classifier knew other CAPTCHA providers, while short-content gates dropped
HTML before preserving challenge evidence. HTTP-status handling from #732
prevents false success but does not by itself identify the challenge provider.

## Decision

Reuse the existing barrier classifier, bounded browser pipeline, typed error
responses and recovery receipts. Recognize actual DataDome challenge iframe
URLs on its exact provider host, not mere mentions or ordinary script
integration. Classify provider HTML before minimum-text and source-status
checks, while keeping unsuccessful source responses out of successful content.

Report DataDome as an unsupported provider with the existing CAPTCHA barrier
type and `BARRIER_DETECTED` envelope. Retain upstream status and safe provider
metadata. Skip unrelated Cloudflare/Turnstile recovery and a fresh browser
when the known unsupported provider remains. Do not misrepresent recognition
as successful solving, and do not disclose challenge URLs or cookies.

Fallback implementations return distinct outcomes for transport unavailability,
service/protocol failures, unsuccessful source responses, and usable-service
but no-content results. The recovery layer preserves these distinctions rather
than interpreting every absent result as service unavailability.

## Alternatives considered

- Treat all short pages as empty: rejected; iframe challenges often have no text.
- Apply existing grid selectors to DataDome: rejected; those selectors implement
  different providers, not DataDome's challenge protocol.
- Send DataDome to FlareSolverr: rejected; this is a Cloudflare-oriented fallback,
  not evidence of DataDome support.
- Add a paid solver, credentials or rotating proxies: outside this change. No
  such dependency is needed to identify the failure honestly.

## Consequences and validation

Operators get a specific capability boundary instead of a misleading outage.
This deliberately does not promise access to Food Lion. Supported-provider
recovery, existing lifecycle deadlines and cleanup still apply. Sanitized unit
fixtures cover short DataDome responses, lookalike hosts, unsupported handling,
HTTP status preservation, fallback outcomes and no false content success.
Live acceptance must distinguish healthy services from successful source access.

This refines [ADR-0015](0015-barrier-classification.md) and
[ADR-0044](0044-autonomous-captcha-recovery.md), without changing the browser
lifecycle ownership in [ADR-0082](0082-bound-embedded-browser-lifecycle.md).
