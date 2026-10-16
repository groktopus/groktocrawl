# Private Document Evidence

Create a research session, attach a local file, and ask questions using selected
document refs alongside ordinary web refs. Parsing uses the existing local-first
Parse service. The API does not read local filesystem paths or arbitrary document
URLs; clients submit bytes or an existing staged upload ID.

```bash
groktocrawl document create-session --ttl 3600 --json
groktocrawl document attach SESSION_ID ./report.pdf --json
groktocrawl document list SESSION_ID --json
groktocrawl document query SESSION_ID "Compare the budget with the web evidence" --ref DOC_REF --ref WEB_REF --json
groktocrawl document show SESSION_ID DOC_REF --start 100 --end 160 --json
groktocrawl document detach SESSION_ID DOC_REF --json
groktocrawl document export-session SESSION_ID --json
groktocrawl document delete-session SESSION_ID --json
```

Use the same API credential throughout. New sessions and staged upload reservations
are bound to a server-derived credential scope. A foreign credential cannot attach,
read, question, export or delete another owned session. Anonymous calls retain the
existing shared anonymous scope; use the server's existing authenticated access for
private work. Existing legacy unowned session reads remain compatible, but private
attachments require a newly created owned session.

## API and MCP

| Action | API | MCP |
|---|---|---|
| Create | `POST /v2/session/create` | `session_create` |
| Attach | multipart `POST /v2/session/{session_id}/documents`, `file` or `upload_id` | `document_attach` (explicit base64 bytes or staged ID) |
| List metadata | `GET /v2/session/{session_id}/documents` | `document_list` |
| Resolve exact text/span | `GET /v2/session/{session_id}/documents/{ref_id}?start=N&end=M` | `document_read` |
| Detach | `DELETE /v2/session/{session_id}/documents/{ref_id}` | `document_detach` |
| Export history | `POST /v2/session/{session_id}/export` | `session_export` |
| Delete all session data | `DELETE /v2/session/{session_id}` | `session_delete` |
| Question | `POST /v2/session/{session_id}/step`, action `query`, params `question` and optional `ref_ids` | `session_query` |

Attachment metadata includes session-local `ref_id`, `snapshot_id`, display filename,
upload-declared media type (`media_type_source: upload-header`), uploaded-byte `file_digest`, exact UTF-8 text `content_digest`, parser
`extraction` metadata and actual extraction `anchors`. Character spans use Unicode
string offsets (start inclusive, end exclusive); reads return an exact `quote_digest`.
Snapshot identity includes both uploaded-byte and extracted-text digests, so different
parser output for the same binary has distinct snapshot and passage identities.
The session-local ref ID and duplicate-byte admission policy remain binary-based.
The retained text is the parser's complete Markdown, without a leading-prefix cap.
This does not certify that every page, image or table in the original binary was
extracted; parser provenance and missing anchors remain explicit.
PDF page count alone does not establish page-to-passage mapping: those PDF outputs
have no page anchor unless the extracted text actually contains a page marker.
DOCX headings become exact section spans when present in the Markdown.

The ordinary session question path returns evidence selection/coverage and citations
for full document and web references. Explicit `ref_ids` restrict selection; unknown
refs are rejected. Model generation still requires the configured LLM, and citations
identify supplied evidence rather than independently verifying every generated claim.
File text and any instructions inside it remain untrusted evidence.

## Bounds and Lifecycle

Admission accepts the existing supported Parse formats, rejects unusable/unsupported
extraction, and requires at most 10 MiB uploaded bytes and 10 MiB extracted UTF-8 text
per document. Extraction metadata is limited to 64 KiB and actual heading/page
anchors to 1024, with explicit rejection on overflow. A session admits at most 20 documents/32 MiB extracted text. Limits are
explicit failures, never silent trimming. Existing staged uploads are single-use and
expire after three hours; staged upload transfer is bounded at the existing Parse
50 MiB maximum, with the tighter 10 MiB document admission limit applied on use.
Duplicate file bytes in one session return the original ref/extraction/name.

Raw file bytes are not archived. Extracted document refs share the session's existing
TTL refresh and deletion. Reads do not extend retention. Detaching removes the ref
from future questions and citation resolution; earlier answers can still contain
quotations. Delete the complete session to remove its history, artifacts and refs.
Foreign/expired/deleted resources return not-found; concurrent session work returns
a retryable conflict. Quota and input failures reject admission before ref commit.

These are expiring session attachments, not a retained PostgreSQL research root or
backup/export promise for original binary files. No hosted OCR, embedding or GPU
request is added by this workflow. Scanned-document extraction follows existing Parse
local-first behavior; baseline fixtures use text-bearing files without OCR.
