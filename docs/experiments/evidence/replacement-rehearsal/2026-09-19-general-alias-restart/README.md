# W9 model-alias correction restart

Status: **checkpoint 0 passed after correcting deployment configuration**

An independent 105-case agent-use audit recorded 83 passes, seven partial
passes, 13 failures, and two blocked cases. Search, scraping, Parse, browser
sessions, bounded crawling, rereads, cancellation, and cross-client behavior
were broadly useful. The failures and blocked cases clustered around rich
search, grounded answers, agent research, and structured extraction.

Candidate logs established a single common cause: the deployment still selected
the retired `local` model group after the approved provider alias had changed to
`general`. The provider rejected `local` and could not select a fallback. This
was configuration drift in the candidate deployment, not thirteen independent
application failures.

The configured alias was corrected without changing source code or client
configuration. Four serial smoke cases then passed: rich search, grounded
answer, focused agent research, and structured extraction. The independent
audit remains retained operational evidence because it correctly demonstrated
that a healthy API and retrieval layer do not prove model-backed readiness.

Changing the configured model is part of the frozen W9 runtime boundary. The
preceding window therefore cannot count toward the seven-day stability gate.
The corrected window began at `2026-09-19T21:46:56.859244Z` and passed all 12
checkpoint operations at `2026-09-19T21:50:36.553057Z`.

The audit also identified separate follow-up work that the alias correction did
not explain: weak web-similarity relevance and metadata, a completed zero-page
crawl, and different CLI/MCP projections of barrier failures. Those findings
remain subject to focused issue review.

The sanitized compatibility, cross-client research, resource, checkpoint, and
follow-up receipts are retained beside this report. Credentials and private
deployment identifiers are absent from this packet.
