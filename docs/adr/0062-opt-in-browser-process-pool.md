# ADR-0062: Opt-In Browser Process Pool with Isolated Contexts

- **Status:** accepted
- **Date:** 2026-09-04

## Context

The browser tier previously launched and closed a Chromium process for every
page. Controlled fixture measurements show that repeated visits can reuse a
browser process while retaining request isolation. CloakBrowser fingerprints
are selected at process launch, so a process cannot be shared across domains
with different fingerprint seeds. Existing deployments also need the current
resource bounds and lifecycle behavior by default.

## Decision

Add an opt-in, bounded process pool in `scraper-svc`. Pool entries are keyed by
the normalized domain fingerprint returned by `fingerprint_seed(url)`. Every
lease creates a new browser context and applies its proxy at context scope;
cookies and pages therefore remain request scoped. Idle and aged entries are
recycled, and startup, cancellation, launch failures, crashes, and shutdown
close their contexts, processes, and controller. Pool settings default to
disabled.

## Consequences

Warm browser-tier requests avoid repeated process startup while preserving the
existing per-request context boundary. The pool adds bounded in-process state
and a small amount of lifecycle coordination. Operators must enable it only
when measured setup savings justify keeping a few browser processes warm.

## Alternatives considered

- Keep per-request launches everywhere: preserves the old startup cost and
  provides no reuse benefit.
- Share one context across requests: leaks cookies and proxy state, so it is
  incompatible with request isolation.
- Use a cross-domain process pool: breaks CloakBrowser's domain fingerprint
  contract.
