# Jev passage-routing rethink (issue #371)

Status: **calibration only; no Jev activation or source-exclusion rule**. This
investigation is confined to the experimental `magnus919/groktocrawl-x` fork.

This is a dated experiment record. Subsequent changes to the fork made
research discovery exhaust eligible returned results by default, subject to an
explicit caller budget. References below to the incumbent early-stop path and
first-8,000-character projection describe the baseline tested on September 21;
they are not claims about the current runtime. The replay scripts operate on
frozen private packets and do not alter live research behavior.

## Why the earlier screen was inadequate

TypeSafe's [RAG-passage cookbook](https://docs.typesafe.ai/cookbooks/classifying_rag_passages)
puts the query and **one passage** in each state. It asks separate Noul questions
about relevance, usable evidence, a challenged query premise, and prompt
injection. The application, not Jev, combines those probabilities into a route.
TypeSafe also [warns](https://docs.typesafe.ai/model-jaggedness/jev-1.13) that
large distracting states and indirect questions reduce accuracy, and that
adversarial text is not safely handled by the model alone. Its
[confidence guidance](https://docs.typesafe.ai/confidence) says action thresholds
depend on the risk and must be tested on the application's own data.

The prior follow-up used one three-way page-value Choice on a deterministic
first-11,000-character selection. The primary RFC 9700 result was classified
`not_valuable`, but the decisive recommendation was beyond that excerpt. A
page-level negative was therefore not an interpretable Jev judgment about the
full page. Nor could the Choice's confidence replace the probability of an
atomic evidence judgment.

## Corrected calibration run

The saved public-web batch for the exact research question—RFC 9700 versus the
OAuth 2.1 draft on PKCE for confidential authorization-code clients—contained
28 distinct URLs from three existing search-result lists. No new Brave search
was made. GroktoCrawl had already scraped 27; one scrape failed with an
anti-bot/retry error and was not called irrelevant. The successful markdown
totaled 855,076 characters. Twenty-one pages exceeded the live research
path's first-8,000-character source projection.

`scripts/run_jev_passage_rethink.py` freezes every successful page into
1,800-character windows with 200-character overlap and asserts full character
coverage. The private packet held 546 query–passage pairs. Each call to pinned
`jev-1.13.0` asked four independent Nouls: subject relevance, a concrete
contribution to **any part** of the query, contradiction of a query premise,
and attempted prompt injection. The last signal is reported separately and is
not a safety boundary. The frozen packet has SHA-256
`5aa231e611dc872206f708277236cf062e3123015e082bdb52ac63bb499a0e77`.
All 546 calls returned valid typed answers; 533,883 input tokens and 46,410
output tokens were reported. Raw page text, per-passage answers, and the key
remain in private local storage, not this repository.

The primary RFC's passage at character offset 16,000 contains the actual
recommendation for confidential clients and received 0.92 probability on the
evidence question. Six of the 20 pages originally labeled valuable had their
highest-scoring passage beyond character 8,000. This shows why a successful
scrape or a first-excerpt screen is not proof that the evidence reaches Jev or
synthesis.

The original labels were best-effort assistant reviews, not independent gold
labels. Of 20 originally marked valuable, 19 had at least one passage at or
above 0.50 on the evidence Noul; the remaining page peaked at 0.38 despite a
relevant explanation. Four of seven originally marked merely related also
crossed 0.50. Manual inspection found direct query-specific claims in at least
three of those four, revealing that the reference labels themselves need
revision. A maximum over 128 passages on a long page is also more prone to a
spurious high score than a maximum over two passages. **No threshold is selected
from these exploratory observations.**

## What this changes about the next experiment

- Scrape every eligible returned result, subject only to explicit caller budget,
  safety barriers, and capacity-based in-flight control. Keep failed/refused
  acquisitions visible. A source count is not the admission policy.
- Segment the full acquired text with stable offsets and validate coverage.
  Jev scores query–passage pairs, not a page's first excerpt. For a composite
  question, evaluate whether a passage contributes to any named facet; a
  passage need not supply a complete answer.
- Keep relevance/evidence, premise challenge, injection, provenance/trust, and
  factual verification distinct. Code owns precedence. Neither a Jev injection
  probability nor a Jev evidence probability overrides deterministic barriers.
- Preserve uncertain or unevaluated passages; do not infer irrelevance from a
  transport failure, missing passage, or borderline score. Do not promote a
  page-level `max` into an exclusion rule without analyzing passage-count bias.
- Before a new validation call, audit source and passage labels without viewing
  that call's Jev answers; freeze a high-recall routing rule on calibration
  material; then compare the incumbent early-stop path, scrape-all without Jev,
  and scrape-all with Jev routing on the **same** new search snapshot. Measure
  required-facet recall and supported answer/citation changes, not only the
  number of scraped or selected pages.

This run demonstrates correct technical use of Jev's atomic decision interface
and exposes a concrete upstream projection loss. It does **not** establish a
user-visible answer improvement, a calibrated exclusion threshold, or safety
classifier reliability. The earlier #360 no-ship decision still applies to
its tested rule; it does not reject this distinct passage-level proposal.

## Holdout protocol, frozen before acquisition

Use one new GroktoCrawl `/v2/search` request with limit 40 for this question:
“For Kubernetes EndpointSlices, how do ready, serving, and terminating
conditions differ during pod termination, and how should a client use them?”
This is a distinct topic from the OAuth calibration data. Scrape every unique
returned URL once, recording failures rather than replacing them. Do not
select URLs by search position or TypeSafe output. This costs one Brave search;
the number of returned results and scrapes is not the search charge.

Before running Jev, review the scraped pages for exact passages that address
each of three facets: (1) condition meanings, (2) termination behavior, and
(3) client traffic/endpoint-selection implication. Mark unsupported or
contradictory claims separately; a passage with a query-specific claim is
still potential research evidence until checked against primary sources.
Record ambiguous labels as ambiguous, not negative.

The calibration-derived **routing** rule is intentionally high-recall: a
passage with evidence probability at least 0.50 is prioritized, while every
passage below that level and every Jev failure remains available for synthesis
or review. Thus the experiment evaluates prioritization and facet coverage,
not source exclusion. Evidence, premise challenge, and injection are separate
signals. Deterministic safety barriers still take precedence. Evaluate
whether prioritized passages cover the manually located facet passages, and
compare this with the incumbent first-8,000-character projection. Do not tune
the rule on the holdout or call this a product-quality win based only on a
score distribution.

### Pre-Jev holdout audit

The one search returned nine unique URLs and all nine scrapes succeeded. Before
opening Jev results, I located these concrete passages in the raw markdown:

| Source | Character offsets | Facets observed | Caveat |
| --- | ---: | --- | --- |
| Kubernetes current EndpointSlices docs (`url-01`) | 3,000–4,800 | All three: serving/terminating/ready definitions and proxy behavior | Primary source; includes the `publishNotReadyAddresses` exception. |
| Kubernetes termination tutorial (`url-03`) | 5,200–6,500 | Termination example with `ready=false`, `serving=true`, `terminating=true` | Shows state, not a complete client policy. |
| Kubernetes v1.32 archived docs (`url-07`) | 3,400–4,750 | Ready versus serving for terminating Pods | Versioned primary source; check current wording before a final answer. |
| KubeSimplify article (`url-02`) | 5,050–5,400 | All three conditions and a traffic implication | Secondary claim; verify against current docs. |
| K8s Recipes (`url-08`) | 5,400–6,150 | Explanatory YAML comments | Secondary and partial; not a policy authority. |

`url-05` duplicates the KubeSimplify article in GitHub markdown; `url-06`
duplicates Kubernetes documentation source in GitHub markdown. They may add no
unique facet. `url-04` and `url-09` were thin scrapes, but are not labeled
negative without a more detailed read. These are best-effort inspection labels,
not independently adjudicated gold data. They were written before the holdout
Jev calls.

### Holdout result

The frozen holdout packet SHA-256 is
`4aa9802cc03a3728e84b440b2008e942e9e213c6fcc24f59411537089f49a1fa`.
Nine successful pages yielded 76 full-coverage passages. All 76 returned
valid typed Jev answers. With the frozen 0.50 prioritization rule, 19 passages
were prioritized; none of the five manually identified facet-bearing source
regions was missed. The current Kubernetes page's strongest passage scored
0.98, the termination tutorial's 0.96, and the archived Kubernetes page's
0.98. The two thin pages were at 0.02 and 0.01, but this is not an exclusion
recommendation.

This holdout confirms that the passage question can surface the independently
located evidence on a different public-web topic. It **does not demonstrate
better final answers**: the strongest current primary-source passage was
already around character 3,200, inside the incumbent first-8,000 projection.
It also does not test false negatives on an adjudicated corpus, robust
injection handling, trustworthiness, citation support, or actual LLM use of
the prioritized passages. The next value test should compare complete
answers and exact citation support on the same frozen search/scrape packet,
with source text held constant; until then, Jev routing remains experimental.
