# Autonomous CAPTCHA Recovery

* Status: accepted
* Deciders: GroktoCrawl maintainers
* Date: 2026-07-15

## Context and Problem Statement

Tier 3 could identify only weak CAPTCHA text signals and returned challenge pages
through normal scraping paths. CAPTCHA widget interaction must remain autonomous,
bounded, private, and compatible with the current Playwright context and cookie
store.

## Decision Drivers

* Do not return or cache challenge content.
* Preserve the existing browser and cookie lifecycle.
* Do not add human takeover, login bypass, proxy rotation, or solver farms.
* Allow operators to select a separate OpenAI-compatible multimodal model.

## Considered Options

* Return detected challenge pages unchanged.
* Add a separate browser service or manual handoff.
* Recover in the existing Tier 3 page with bounded provider-aware actions.

## Decision Outcome

Use centralized definitive DOM signatures for Turnstile, reCAPTCHA, hCaptcha,
and a conservative generic widget. Tier 3 passively waits, attempts a visible
checkbox, then makes at most two image-grid attempts. Grid screenshots exist only
in memory and are disclosed only to the configured `CAPTCHA_VISION_*` provider;
they are not logged, cached, returned, or written to disk. Unsupported vision
responses disable vision for the process lifetime.

Unresolved challenges return `CAPTCHA_UNRESOLVED` with safe provider, strategy,
and confidence details. FlareSolverr remains a Cloudflare/Turnstile fallback; it
is not presented as a CAPTCHA solver.

CloakBrowser `0.4.10` is used as an optional direct Playwright executable with a
stable domain-derived fingerprint. Its MIT Python wrapper is distinct from the
non-redistributable binary: the image includes no binary or license key, mounts
`/root/.cloakbrowser`, and attempts the official runtime download at startup.
Failure falls back to stock Playwright Chromium.

## Consequences

CAPTCHA attempts may add bounded latency and cannot guarantee a successful solve.
Operators remain responsible for authorization, target terms, robots policy, and
rate limits.

## Links

* [ADR-0015](0015-barrier-classification.md)
* [ADR-0011](0011-stealth-playwright-configuration.md)
