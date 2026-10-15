# W11 isolated recovery evidence

This packet closes the interruption boundaries that were missing from the
initial W11 contract summary. It uses the SlopSearX 0.5.0 runtime pinned by W11
and proof revision `ebfdd7d463515cd92b8309706aee1b6a095bb78e`.

## Results

- SlopSearX fault injection terminated a worker immediately after an engine
  response and immediately after snapshot persistence. Recovery retained the
  uncertain charge, marked the unfinished attempt interrupted, preserved a
  written snapshot, and completed without another engine call. These cases are
  included in the 33-case reference-contract summary.
- GroktoCrawl fixture injection and a live process kill covered an ambiguous
  downstream capture. In the live case, the page capture completed behind a
  loopback delay proxy, but the experiment process was terminated before it
  received the response. Resumption reused the search and handoff checkpoints.
  The ambiguous page request remained counted and the capture was tried once
  more.
- The live isolated arm terminated immediately after SlopSearX accepted a
  receipt. Resumption made no additional search, result-read, or capture call.
  The repeated receipt submission returned the original receipt identity, one
  receipt remained retained, the manifest stayed linked, and the hard gate
  passed.

The secret-free live outcomes are in
[during-capture.json](during-capture.json) and
[after-receipt.json](after-receipt.json). The private checkpoints retain the
results, URLs, captured material, and receipt bodies outside the repository.

## Limits

HTTP page retrieval can be ambiguous at interruption, so the protocol permits
one visible retry. The live test therefore performed two successful captures,
one before termination and one after recovery. It did not repeat search or
result expansion. These cases prove recovery mechanics; they do not turn a
receipt into a verification judgment.
