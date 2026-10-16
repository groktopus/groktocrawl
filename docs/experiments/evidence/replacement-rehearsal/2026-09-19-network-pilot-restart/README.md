# W9 real-use Hermes pilot restart

Status: **network exposure verified; checkpoint 0 passed**

The owner chose normal client use as the operational workload rather than leaving
the candidate mostly idle between synthetic checkpoints. The API and MCP transport
were therefore published on the candidate host host ports 18080 and 18002. The API
continues to require the candidate key. MCP Host values are limited to the
declared loopback, home-lab, and Tailscale identities.

The first edit intended to make the bind address configurable but local shell
expansion wrote `127.0.0.1` back into the deployed Compose file. Both services
remained healthy and loopback-only, and remote probes correctly failed. The
correction first proved the LAN-specific listeners on the host, then bound the
ports on all host interfaces so the client machine's existing Tailscale route
could reach them. From that machine:

- authenticated `the authenticated API health endpoint over the trusted overlay network` returned
  aggregate status `ok`;
- `the MCP health endpoint over the trusted overlay network` returned MCP status `ok`
  with the agent service connected;
- Compose reported `0.0.0.0:18080` and `0.0.0.0:18002`;
- all candidate dependencies remained healthy.

This is an intentional operational-configuration change, not observation-tooling
maintenance. The loopback-only window ended, its evidence remains retained, and
the seven-day clock plus successful-operation count restarted at
`2026-09-19T13:42:50Z`. The candidate source and runtime image revisions did
not change.

The first guarded checkpoint attempt stopped before traffic because the private
Compose environment quotes `CANDIDATE_IMAGE_TAG` and the durability validator
compared the literal quotes to the Git-revision pattern. Compose itself had
correctly used the unquoted value. The failed packet is retained; the validator
now applies the same outer-quote handling to all env-file values before
validation.

The second guarded attempt reached the compatibility runner but received HTTP
403 on its first scrape. The checkpoint wrapper had exported the key only as
`CANDIDATE_API_KEY`, while the compatibility runner intentionally reads
`GROKTOCRAWL_API_KEY`. Earlier shells could mask this mismatch by already
exporting the latter. The wrapper now supplies both documented consumer names
from the single effective container value, without placing the key in arguments
or receipts. This attempt is also retained as failed setup evidence.


## Checkpoint 0 result

The guarded checkpoint completed at `2026-09-19T13:51:26Z` against the frozen
candidate and recorded 12 successful representative operations. The compatibility,
cross-client research, and resource receipts are retained beside this report with
their checksums in `checkpoint.json`. No candidate-only contract failure, artifact
divergence, storage-authority alarm, or rollback-copy inconsistency was observed.

The failed setup attempts remain under `failed-attempts/`; neither is counted as a
successful operation. The real-use window now has one of three required checkpoints
and 12 of at least 30 required successful operations.
