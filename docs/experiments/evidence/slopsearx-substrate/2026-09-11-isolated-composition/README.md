# Isolated workflow-composition evidence

This packet records one representative live SlopSearX 0.5 composition journey
in a dedicated Compose project with separate Valkey storage and only the
`research` and `retrieval_receipts` grants enabled.

The successful journey composed an immutable search snapshot into one bounded
research attempt, replayed the start request without creating a second job,
exported the completed job as a 19-item research manifest, and followed an
explicit `derived_from` edge back to the source snapshot. The manifest retained
the contract's statement that attributed observations are not verified.

An earlier setup start attempted the default MCP host port even though the
private environment selected a unique port. The MCP container never started;
it was recreated from the verified rendered Compose configuration before the
preflight. A first research journey using a source that returned no results is
also excluded: the composed job completed with no evidence and manifest export
correctly refused to create an empty manifest. Neither excluded attempt is
counted as a passing composition journey.

`preflight.json` contains the redacted capability and grant-boundary record.
`live-composition.json` contains hashes and outcome fields only. Credentials,
queries, result bodies, artifact identifiers, endpoints, ports, and private
network details remain outside the repository.
