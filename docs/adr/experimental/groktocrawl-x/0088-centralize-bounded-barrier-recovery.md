# Centralize Bounded Barrier Recovery at the Scrape Boundary

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-20
- Scope: W13 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#363](https://github.com/magnus919/groktocrawl-x/issues/363)
- Extends: [ADR-0010](../../0010-five-tier-scraper-with-llm-recovery.md), [ADR-0015](../../0015-barrier-classification.md), [ADR-0044](../../0044-autonomous-captcha-recovery.md)

## Context and Problem Statement

GroktoCrawl already detects challenge pages and has several countermeasures:
Playwright waiting and stealth, in-page CAPTCHA recovery, FlareSolverr, an
independent browser service, and a guarded proxy configuration. Those mechanisms
were added at different times and are reached through different branches. Some
callers also implement their own lightweight-to-browser retry. As a result, one
public path can stop after a CAPTCHA refusal while another path tries an
additional configured countermeasure.

Calling agents should not need to understand GroktoCrawl's internal recovery
tools or decide when to invoke them. They commonly treat the first typed barrier
as final.

## Decision Drivers

- Make a single scrape request behave consistently through HTTP, CLI, MCP,
  crawl, search enrichment, and research.
- Exhaust every applicable configured countermeasure before terminal refusal.
- Keep retries finite, deterministic, observable, and safe to cancel.
- Never pass challenge content to an LLM as research evidence.
- Preserve typed errors and Firecrawl-compatible success payloads.
- Avoid exposing service addresses, credentials, proxy configuration, or raw
  challenge material in diagnostics.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Require callers to choose recovery tools | No central refactor | Repeats the current failure; callers need internal knowledge and give up early |
| Add retries independently to each public surface | Can optimize each endpoint | Policy drifts, duplicate attempts, inconsistent errors, and a larger test matrix |
| Centralize a classified recovery ladder in scraper-svc | One policy inherited by every caller; bounded and inspectable | Scrapes that encounter barriers can take longer and consume more browser capacity |
| Try every tool for every failure | Simple mental model | Wastes capacity, worsens rate limits, and invokes tools that cannot solve the classified failure |

## Decision

The scraper service owns one bounded barrier-recovery ladder. Public and
internal consumers request content once; they do not need to select a
countermeasure.

After Playwright and its in-page CAPTCHA resolver encounter a barrier, the
service selects strategies by applicability:

1. Playwright challenge waiting and CAPTCHA strategies remain the first browser
   attempt.
2. FlareSolverr runs once for Cloudflare and Turnstile barriers, and for an
   unclassified browser failure where Cloudflare cannot be ruled out.
3. A fresh browser-svc session runs once for interactive barriers that can
   plausibly benefit from an independent browser context.
4. Rate limiting and empty content do not trigger unrelated browser tools.
   Their retry behavior remains governed by their existing typed contracts.

The ladder stops on the first acceptable content result. Each strategy runs at
most once per ladder. When all applicable strategies fail, the service returns
the original typed classification where possible and marks the recovery as
exhausted.

Every recovery produces a sanitized receipt containing the barrier class,
provider when known, strategy names, outcomes, and whether recovery was
exhausted. It contains no credentials, service addresses, proxy values,
screenshots, raw challenge HTML, or personal deployment details.

Challenge content remains excluded from LLM recovery and downstream research
synthesis. This record does not authorize login bypass, human-account takeover,
proxy-pool rotation, paid solver farms, or unbounded retries.

## Confirmation

- Unit tests cover applicability, bounded ordering, recovery success, and
  terminal exhaustion.
- Service tests confirm the recovery receipt survives the scraper HTTP error
  boundary and representative agent, crawl, CLI, and MCP consumers preserve
  the typed refusal.
- Existing barrier-consumer tests continue to prove challenge content cannot
  reach synthesis.
- CI remains the merge gate. The W9 live candidate stays frozen; W13 is not
  deployed into that observation window.

## Consequences

- Callers become simpler and no longer determine whether recovery is complete.
- Barrier encounters can use one additional browser session and therefore have
  higher worst-case latency and browser demand.
- Operators and agents can distinguish an early failure from a bounded,
  exhausted recovery attempt.
- New countermeasures must be added to the shared applicability policy and its
  bounded tests, rather than wired into one endpoint.
- This is an experimental-fork decision and does not change mainline
  GroktoCrawl policy.
