# W1 replacement packet review — 2026-09-10

Status: **structurally valid and isolation-approved; comparison not authorized**

Candidate B was frozen before this packet was created. Hermes then curated the
packet in a private directory outside the implementation workspace. The
implementation agent has not read or displayed its source text, questions, or
expected answers.

The repository records only the information needed to review the evaluation
boundary:

- 30 unique cases from 9 source records;
- 11 adverse or abstention cases;
- 8 topic categories and 30 distinct template families;
- no overlap detected with the exposed development corpus by the packet validator;
- corpus digest
  `sha256:99857f4484492e787adc62aab81189551f66ebcc3c9cd42f5bfdf7dcdc6dc796`;
- access-log digest
  `sha256:0f459327ffd3ced393ee653030ca5664ea985b228db10a2d947ddb9f9dc288dd`;
- validation-record digest
  `sha256:72aa09aa66df518151e5ef3b111fdbf78e39508c9e827390f18389bb58a51885`.

The first structural check found incorrect SHA-256 metadata on all nine source
records. An automated repair recomputed those fields from the already sealed UTF-8
text without printing any content. That access was added to the private access log,
the packet was resealed, and validation then passed with no structural errors.

On 2026-09-10, Magnus Hedemark approved the replacement packet's curator, access
history, and isolation mechanism. The private approval record is bound to the
three digests above and has digest
`sha256:946baaf7a32f8faf18d9ccdea8fd829ea1acb0a11738a4ebd81ff932cd04bad2`.
The packet is now eligible to serve as held-out evidence for the frozen candidate.
This approval does not authorize the scored comparison, production adoption, or
replacement of mainline GroktoCrawl.
