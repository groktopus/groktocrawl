# W9 live-feature repair restart

Status: **checkpoint 0 passed after candidate runtime repair**

A real client audit found three candidate defects: streaming search failed before
sending a request, Parse was absent from the candidate deployment, and semantic
web-similarity calls timed out before the deployed embedding service completed.
The fixes were reviewed and merged through PR #341. Live verification then found
that the staged Parse path discarded filename and media-type metadata while
atomically consuming an upload. PR #343 repaired that path and added its missing
failure contract.

The final candidate runtime began at `2026-09-19T18:12:36.512533Z`. The API and
MCP services remained healthy, and the client configuration was not changed.
Direct Parse, staged Parse, streaming search, and web similarity all passed on
the deployed candidate. Web similarity completed in 26 seconds, within the new
60-second semantic-client boundary.

Because these were candidate code and service-topology changes, the previous
operational window cannot count toward the seven-day stability requirement. Its
12 successful requests remain retained evidence. The final runtime restarted the
clock and successful-operation count, then passed checkpoint 0 at
`2026-09-19T18:15:21.395820Z` with all 12 declared operations successful.

The sanitized compatibility, cross-client research, resource, and checkpoint
receipts are retained beside this report. The API credential and private network
identifiers are absent from this packet.
