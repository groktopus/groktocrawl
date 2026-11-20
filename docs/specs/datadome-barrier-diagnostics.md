# DataDome barrier recognition and truthful recovery

## Problem and evidence

The November 17, 2026 Food Lion production probe returned HTTP 403 with an
iframe titled `DataDome CAPTCHA` from `geo.captcha-delivery.com` and no body
text. The scraper reported an unknown barrier and labelled working fallback
services unavailable. Short challenge responses were discarded before provider
classification. Upstream browser HTTP-error handling (#732) does not identify
this provider.

## Scope and acceptance criteria

1. Given a DataDome challenge iframe (including query mode `bv` or `fe`), when
   classifying empty or short text, then identify provider `datadome` at high
   confidence. Plain prose, escaped code examples, unrelated iframes and
   lookalike domains must not trigger this definitive match.
2. Given that provider, then preserve a typed barrier and upstream HTTP status
   before content-length gates. State that this provider is unsupported, never
   claim a solve, and do not try unrelated FlareSolverr or fresh-browser retries.
3. Given a fallback service that responds with an empty/thin document, source
   HTTP error, malformed response, or service error, then retain that distinction
   in recovery diagnostics. Reserve unavailable for connection/timeout failures.
   Do not return challenge HTML, cookies or challenge URL tokens in diagnostics.
4. Given existing supported CAPTCHA fixtures and successful content, then retain
   their recovery and quality checks. All created browser sessions still close.

## Contracts and boundaries

Existing failure envelopes and HTTP 502 behavior remain; `barrier.provider`,
`barrier.supported`, `http_status` and recovery attempt outcomes explain failure.
DataDome uses the existing `captcha` barrier type and `BARRIER_DETECTED` error
code. Fallback failure outcomes are bounded strings, not exception messages.
No new credentials, paid service, proxy rotation, security-policy changes,
CAPTCHA vendor integrations, or repeated live attempts are part of this change.

The current resolver implements reCAPTCHA, hCaptcha and Turnstile widget/grid
flows only. DataDome's different challenge protocol is not implemented. The
observed `bv` mode is retained as evidence, not proof of a solvable puzzle or a
claim that changing IP would succeed. Classification and honest failure are
not equivalent to bypassing the site.

## Plan and verification

Add sanitized regression fixtures first; verify they fail on main. Implement
provider detection and shared sanitized failure construction, move classification
before extraction gates, and preserve outcomes through recovery and the API.
Run focused browser/CAPTCHA/regression suites, lint and repository surface checks,
then broader fast tests. A bounded post-deployment test must report the actual
provider/outcome; content success requires substantive non-challenge content.
