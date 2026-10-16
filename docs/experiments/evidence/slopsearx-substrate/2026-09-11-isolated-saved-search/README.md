# W11 isolated saved-search evidence

This packet evaluates SlopSearX saved-search scheduling and event delivery as a
separate W11 capability slice. The isolated arm enabled only `saved_searches`
and `saved_search_events`; the other seven specialist grants were denied before
dispatch.

The live lifecycle used one explicitly scoped package search. It produced an
immediate baseline and a scheduled comparison one interval later. The second
observation found no source changes and emitted no change events. Pausing held
the report count at two for a complete interval. Event reads demonstrated
at-least-once replay before acknowledgement, acknowledgement was idempotent,
the post-ack read was empty, and resume plus deletion completed successfully.

- [preflight.json](preflight.json) contains the redacted least-grant check.
- [live-lifecycle.json](live-lifecycle.json) contains hashes, counts, event
  types, and gate outcomes. Query text, result content, saved-search identity,
  report bodies, and event bodies remain in the private packet.

The first evaluation of this retained run failed because the harness assumed
reports were oldest-first. The API returns newest-first. The evaluator was
corrected to identify baseline and comparison reports by their explicit status,
then the same evidence was reanalyzed; no search was rerun. Controlled added,
changed, and not-observed source cases remain deterministic contract evidence.
This live case establishes scheduling and delivery mechanics, not change-
detection accuracy over an evolving external source.
