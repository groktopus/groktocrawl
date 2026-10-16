# W11 isolated dependency-dossier evidence

This packet evaluates the SlopSearX dependency dossier as a separate, focused
W11 capability. The isolated arm enabled `dependency_dossier`, `research`, and
`security`, which are the documented composite grants for this workflow. The
other six specialist grants were denied before dispatch.

The live case supplied an exact Python package, requested version, and candidate
repository. Package metadata resolved, while the observed registry version
differed from the requested version. Repository acquisition failed and no
advisory leads were observed. SlopSearX returned a stable `partial` dossier that
named the missing repository coverage, did not claim package ownership, left
advisory applicability unevaluated, retained three limitations, and stayed
within its adapter and result ceilings. Starting the same request again reused
the original job.

- [preflight.json](preflight.json) contains the redacted least-grant check.
- [live-dossier.json](live-dossier.json) contains hashes, section states,
  bounded-work counters, and gate outcomes. Package results, repository records,
  advisory payloads, job identity, and credentials remain in the private packet.

The first Compose invocation omitted the arm environment file and encountered a
port collision before the MCP or research workflow started. It is excluded as a
setup error and retained in the research log. The corrected run used the frozen
arm configuration.

This result demonstrates honest partial reporting, stable replay, explicit
coverage gaps, and conservative identity claims. It does not establish that the
dossier found every relevant package, repository, or advisory fact, and it does
not stand in for the open-domain W11 research comparison.
