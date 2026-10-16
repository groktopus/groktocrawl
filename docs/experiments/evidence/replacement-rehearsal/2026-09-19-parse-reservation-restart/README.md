# W9 staged-Parse reservation restart

Status: **checkpoint 0 passed after restoring the public staged-Parse flow**

The live-feature repair window exposed one final boundary in the staged Parse
workflow: the upload endpoint accepted only a previously reserved identifier,
but the public reservation route had been lost when Parse moved into its own
router. The checked-in CLI therefore could not initiate a staged upload without
an internal storage write.

PR #346 restored the authenticated upload-reservation route, taught the CLI to
reserve an identifier automatically, retained explicit identifiers for
compatible clients, and recorded the reservation route as internal MCP transfer
plumbing. All required CI checks passed, including the full integration and
PostgreSQL durability suites.

The merged candidate runtime began at `2026-09-19T19:00:53.139536Z`. Live
verification used the checked-in CLI and the public reserve, upload, and parse
interfaces. It did not seed the upload store directly. Upload and parsing
passed, the document text and original filename were preserved, and the API and
MCP services remained healthy. Client configuration was not changed.

Because this was a candidate code change, the preceding repaired window cannot
count toward the seven-day stability requirement. Its 12 successful requests
remain retained evidence. The final window restarted the clock and request
count, then passed checkpoint 0 at `2026-09-19T19:03:37.101501Z` with all 12
declared operations successful.

The sanitized compatibility, cross-client research, resource, checkpoint, and
live-feature receipts are retained beside this report. Credentials and private
network identifiers are absent from this packet.
