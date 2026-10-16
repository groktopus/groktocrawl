# Jev continuation and saved-search case freeze

Status: **frozen before live calls; no Jev calls recorded at this revision.**

This signed case-freeze supplement pins the two feasibility-screen packets for issues #370 and #373. It is not a product-quality result. The exact input packet, including public text and evaluation-only labels/replay pools, is stored outside the repository at `/private/tmp/gcx-jev-case-freeze-1003.json` with mode `0600`; the provider runner sends only each case’s `state` and its frozen question, never `evaluation` data.

- Protocol revision: `8044320200e04ceaafcee6b771abd52f1bdb6392`
- Private case packet SHA-256: `7d519f76c9a8f6318218a75490abc413b2390722192f9a01b61058eb11f842aa`
- Case count: 10 total; 4 continuation and 6 monitoring
- Frozen model: `jev-1.13.0`
- Live budget: at most 50 combined requests, sequential, no retries; this packet plans 10.
- Reference provenance: assistant best-effort assessments; not independent gold labels. Synthetic controls are explicitly separate.

## #370 continuation packet

The first-pass source material uses the existing `orchestration.json` SlopSearX result snapshot for query “LangGraph durable execution checkpoint replay interrupt research agents”; snapshot SHA-256: `bfc80321cdf71bd690175b2e5e76102b6a9b7c3fa98cf9342974c4599e84cc3a`. No additional search request was issued. The acquired first-pass pages were the two public result URLs for Vadim Nicolai’s LangGraph durable-execution article and Burak Degirmencioglu’s Medium article. The latter contains mutually inconsistent descriptions of interrupt replay. Official LangGraph documentation and the returned arXiv candidate were held evaluator-only as follow-up evidence. The question seen by Jev excludes those follow-up candidates.

| Case | Split / stratum | Reference obligation status | Reference search decision | Model input SHA-256 |
|---|---|---|---|---|
| `370-cal-01` | calibration / met | met | no | `eabad98f36224e6fcaf5e856c3b7145f04d2606ee263cf4e4cc478aa10260537` |
| `370-val-01` | validation / contradiction | contradiction | yes | `79e42624d9b859d08616c6ba49d43d7ae9d16081f0bf17f8e86d32dfe7552468` |
| `370-val-02` | validation / unmet | unmet | yes | `ed36d07f9a8f4d6547bb4b4815c60a00345ebd6a485f98612645c9e4a6b5becb` |
| `370-val-03` | validation / unanswerable | unanswerable | uncertain | `ca70a1431a6336b7aa9e770b34f91fdde5b1496324a6accce72ed233124ef23f` |

Status definitions: `met` means the first pass already supports the obligation; `unmet` means a concrete obligation remains uncovered; `contradiction` means the acquired first-pass sources conflict on the obligation; `unanswerable` means the supplied public state cannot responsibly answer an instance-specific question. An unanswerable case is not an instruction to search indefinitely.

The follow-up pool is replay/evaluator-only. Its evidence gain will be reported separately from whether a decision-only search recommendation matches the best-effort reference. Since there is no new search result set, this packet cannot measure the quality of a newly formulated query or the quality of a new live retrieval.

## #373 monitoring packet

Three calibration controls are constructed and reported apart from the public-document cases: boilerplate-only footer, a version-specific verification change, and an after-fetch failure (which is unevaluated, not irrelevant). The validation set has three genuine page revisions from public GroktoCrawl X Git history. Each validation state contains the complete exact before/after Git blob, the exact unified diff, full-blob SHA-256 hashes, a diff identity hash, and stable zero-based half-open line and byte ranges.

| Case | Split / stratum | Reference label | Page and immutable revisions | Model input SHA-256 |
|---|---|---|---|---|
| `373-cal-synthetic-boilerplate` | calibration / synthetic-boilerplate | immaterial | `synthetic-boilerplate-01`; `synthetic → synthetic` | `e780669f5e2fa02c722e49ed651273b33101c92a0aa18fd9dfd7877340218dff` |
| `373-cal-synthetic-version` | calibration / synthetic-version-specific | material | `synthetic-version-01`; `synthetic → synthetic` | `243fc7aa75b9220122ad3258c6db90f40113a5355199321469b254579d83bdc8` |
| `373-cal-synthetic-fetchfail` | calibration / synthetic-fetch-failure | unevaluated | `synthetic-fetch-failure-01`; `synthetic → synthetic` | `70036724acb009c1bb48b324929ada2ac768e8384f21738cebb8391805da293f` |
| `373-val-01` | validation / real-contract-change | material | `README.md`; `57f4ee8b37a4d9e27c6890b4d003f76401997bd0 → 3e2900ae67b09d15c5d40a983621ec0025e3cd34` | `539b82296ec8643d9f4a9da7896d56e4a986949fb6323dbac8c43d7bebeb4644` |
| `373-val-02` | validation / real-consequence-change | material | `docs/adr/0093-retain-source-search-provenance.md`; `3e2900ae67b09d15c5d40a983621ec0025e3cd34 → 05ebb737fe8c93c6d6bf638fd03bff5e62c2fc43` | `3cfd377f38f6cd861174072828b7c015a25a5a91847bc06331dd3175c2512749` |
| `373-val-03` | validation / real-monitoring-contract-change | material | `docs/experiments/upstream-search-reconciliation.md`; `78179a3d0f9f78b5dacf741c2a32ab264950e483 → 10be0d731f7091936f4f3b0c9b40d3babd59135c` | `3bd916d2bbf97a69a4e31801859ac57127f502ae41993cd8808abe5c7824f19e` |

The three real validation pairs are: README source metadata-contract disclosure (`57f4ee8b37a4d9e27c6890b4d003f76401997bd0` → `3e2900ae67b09d15c5d40a983621ec0025e3cd34`); ADR-0093 consequences addition (`3e2900ae67b09d15c5d40a983621ec0025e3cd34` → `05ebb737fe8c93c6d6bf638fd03bff5e62c2fc43`); and upstream research/monitoring scope addition (`78179a3d0f9f78b5dacf741c2a32ab264950e483` → `10be0d731f7091936f4f3b0c9b40d3babd59135c`). These are documentation authored in the project context; this small, selected set carries author-context and topic-selection bias and is not a general monitor-quality sample.

The exact answer labels are frozen before provider calls. Disagreements will be shown by case and stratum, with provider failures counted as unevaluated. No validation thresholds will be tuned from these ten cases.
