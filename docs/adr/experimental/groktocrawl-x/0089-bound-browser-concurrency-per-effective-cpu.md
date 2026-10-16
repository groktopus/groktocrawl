# Bound Browser Concurrency per Effective CPU

- Status: accepted for bounded experimentation; implementation pending
- Decider: Magnus Hedemark
- Date: 2026-09-21
- Scope: browser-tier scraper capacity experiments in `magnus919/groktocrawl-x`; not a mainline deployment-default change
- Related: [ADR-0051](../../0051-global-admission-control-and-cancellation.md), [ADR-0061](../../0061-scraper-scaleout-capacity.md), [ADR-0062](../../0062-opt-in-browser-process-pool.md), [issue #371](https://github.com/magnus919/groktocrawl-x/issues/371)

## Context and Problem Statement

Research may need to scrape every source that can contribute to a composite answer. An arbitrary *source count* is not a substitute for a resource-capacity policy. Browser rendering, however, consumes CPU, memory, process slots, and time; unbounded simultaneous pages can raise tail latency without increasing completed scrapes.

The existing opt-in scale-out contract in ADR-0061 assigns four browser lifecycles per scraper replica and up to four replicas. That number was a conservative bound, not a measured limit for this workload. The scraper currently accepts `SCRAPER_MAX_BROWSER_CONCURRENCY` values from 1 through 32 per service. A single service therefore cannot presently express 64 active browser pages on four CPUs.

An isolated one-core scraper HTTP test found that 32 in-flight pages gave only a modest throughput gain over 16 when each synthetic origin response waited 250 ms, while median p95 service time increased from about 2.2 to 4.1 seconds. The decider explicitly prefers the lower-latency 16-page ceiling to extracting the last few percent of throughput at 32.

## Decision Drivers

- Bound browser resource use without discarding valuable sources.
- Favor tail latency over the small additional throughput observed at 32 pages per CPU.
- Size against CPU *available to the scraper*, not host-reported logical CPU count.
- Preserve independent memory, process, admission, and per-origin pacing controls.
- Allow aggregate capacity to grow when more dedicated CPU cores and scraper replicas are available.

## Considered Options

| Option | Benefit | Cost or reason not selected |
|---|---|---|
| Keep four browser slots per replica | Existing conservative setting | Leaves measured one-core throughput underused for delayed pages |
| Allow up to 16 in-flight browser pages per effective dedicated CPU | Near-ceiling throughput in the measured delayed-page case with substantially less latency than 32 | Requires memory and multi-core validation; not a universal throughput guarantee |
| Allow 32 per CPU | Slightly higher synthetic throughput | Roughly doubles p95 service time in the measured one-core delayed-page case |
| Cap the number of search results or sources scraped | Bounds a research request directly | Can discard relevant evidence and is not the resource bound being decided here |

## Decision

For experimental browser-heavy scraper deployments, **16 simultaneously active browser pages per effective dedicated CPU core is the ceiling**, subject to stricter memory, process, per-origin, or deployment limits. Excess work waits for admission or follows the existing explicit overload/cancellation contract; this decision does not cap how many sources may be scraped over the lifetime of a research request. The ceiling is not a target that every request must fill.

Effective CPU capacity must reflect the scraper's actual allocation (for example, container quota and CPU affinity/cpuset), not `os.cpu_count()` on the host. The measured CPU allocations were separate P-core logical CPUs; the experiment does not establish equal capacity for hyperthread siblings, efficiency cores, fractional CPU quotas, or noisy shared cores. Those cases require conservative configuration and new evidence.

For the bounded scale-out experiment, assign one scraper replica to each dedicated CPU core and cap each replica at 16 browser lifecycles. This permits measured aggregate ceilings of 16, 32, and 64 across one, two, and four replicas without exceeding the current per-service configuration maximum of 32. It does **not** claim that a single four-CPU scraper process has been validated at 64 or authorize raising the per-service maximum solely to make that configuration possible. ADR-0061's gateway, pacing, and other safety decisions remain in force; this record replaces only its four-browser-lifecycles-per-replica capacity assumption within the experimental fork.

The decision does not enable browser pooling by default, alter mainline GroktoCrawl, select sources before scraping, or establish a production sizing rule for real websites. Configuration and deployment changes are follow-on work and must be validated before promotion.

## Confirmation and Evidence

- [One-core HTTP screen](https://github.com/magnus919/groktocrawl-x/issues/371#issuecomment-5762129433): at a 250 ms synthetic origin delay, three-repeat medians were 7.46 pages/s and 2.20 s p95 service time at 16 in flight, versus 7.86 pages/s and 4.06 s p95 at 32. The zero-delay fixture reached about 96% of peak throughput with one in-flight page. No errors occurred.
- [One/two/four-core scale-out screen](https://github.com/magnus919/groktocrawl-x/issues/371#issuecomment-5762226754): one-CPU-pinned replicas, each capped at 16 browser pages, delivered median 7.54, 15.05, and 29.25 pages/s with p95 service time 2.24, 2.20, and 2.33 s respectively. All 1,344 scrapes succeeded through Playwright. This was a short, isolated, single-domain synthetic test using stock Chromium, not a soak test or real-site capacity guarantee.
- Before a deployment claims this rule, a change-triggered configuration check should verify the effective-CPU calculation and the per-replica browser ceiling; a representative load and soak test should inspect throughput, p95/p99 latency, errors, memory trend, and origin pacing. These checks are **planned**, not yet implemented or passed. A material change in browser engine, page mix, CPU type, or topology requires renewed measurement.

## Consequences

- The experimental fork has an explicit latency-conscious concurrency ceiling, separate from source selection or scrape quotas.
- More dedicated CPU cores can increase aggregate scrape throughput when deployed as independently bounded replicas; the measured four-replica result was about 97% of ideal linear scaling relative to the one-replica median.
- Queueing and request latency still grow under overload. A source is not dropped merely because all browser slots are busy, but the existing admission and timeout contracts still apply.
- Memory, PID, network, origin-policy, and downstream processing capacity can become the next bottlenecks. The synthetic per-replica memory peak was below 0.85 GiB, but real pages may be much heavier; 2.5 GiB test limits are not endorsed as production sizing.
- A single multi-core process, shared or fractional CPUs, mixed domains, real sites, and sustained operation remain unvalidated. The accepted experimental ceiling is not proof of production readiness.
