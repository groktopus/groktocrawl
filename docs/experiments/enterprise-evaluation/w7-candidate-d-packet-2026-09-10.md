# W7 Candidate D replacement packet — 2026-09-10

Status: **fresh private packet validated; isolation approved by the maintainer**

After Candidate D froze, a separate Hermes curator worked in an isolated directory
and was explicitly barred from the repository, candidate code, prompts, outputs,
and prior packets. The curator consulted 15 public URLs and two general methodology
files. Its access log states that no candidate implementation or output was
accessed.

The packet contains 30 cases and 15 captured sources for enterprise agentic
engineering software factories. All cases use split `held_out_candidate_d`.
Categories are balanced across architecture, security, governance, operations,
evaluation, data, organization, and economics, with three or four cases each.
It includes deliberate contradiction, insufficient-evidence, scope, time, and
high-consequence cases.

## Private identities

- corpus: `sha256:10e61e5b544de64fd0f5b0e1a9caeabaa4bbe686f4c4eb4535ef998d78103c10`;
- access log: `sha256:673f3a66e74d9d84fd82735c90881072c2e771588b82ab87975cf2afbfc95cd4`;
- private summary: `sha256:44af23ee9a4bfed32a2c766aabb25b5dce8681bcd20fc89be631d88a2c414e80`.

The private directory is mode 700 and each file is mode 600. The fail-closed
validator confirmed JSON shape, exact file identities, timestamps, unique IDs,
public HTTPS source identities, exact source-text digests, reference closure,
case count, category balance, adversarial coverage, and the isolation declaration.
It printed only aggregate counts.

## Assessment and decision

The recorded evidence supports treating this packet as independently curated and
unseen by Candidate D before its freeze. The maintainer approved the isolation
boundary on 2026-09-10. This approval does not accept ADR-0080 or adopt Candidate
D.

Raw questions and captured source text remain private until candidate outputs are
frozen. No scored comparison has started.
