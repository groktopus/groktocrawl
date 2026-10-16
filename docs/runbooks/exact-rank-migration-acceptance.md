# Optional exact-rank migration acceptance

This offline protocol is separate from the strict shadow comparator. Preserve the
original strict query receipt, including failures. Never relabel it as passed.
Approximate Qdrant search can return different neighbors; an exact oracle can
establish rank equivalence while accepting only score ties at the fixed 0.0001
tolerance. The runtime comparator and serving configuration remain unchanged.

Before using this alternative for promotion, review its semantics explicitly.
Require a quiet corpus and a successful full stored-ID, metadata and vector parity
receipt. Guard the intended containers, image identities, model, database scope
and configured service credentials. Retain those identities and both receipts in
private operator evidence; never commit deployment details or corpus contents.

Copy the reviewed `scripts/accept_exact_rank_migration.py` into the intended
semantic service container (it imports the installed `shadow_pgvector` module).
Run it with the same deterministic sample SHA-256 recorded by the original strict
gate and the reviewed tooling revision:

```sh
timeout 900 python /tmp/accept_exact_rank_migration.py \
  --expected-sample-sha256 "$SAMPLE_SHA256" --runtime-revision "$RUNTIME_REVISION" \
  --tooling-revision "$TOOLING_REVISION"
```

The caller must enforce the 900-second timeout; the runner also installs a
900-second alarm. Require its zero exit status and `passed: true` receipt. Missing required operations counter declaration, malformed metrics, counter increases or resets, sample drift, missing/duplicate hits,
nonfinite numbers, score disagreement, material inversions and material boundary
substitutions fail closed. Run only with full shadow sampling. Each of 100 stored
vectors queries exact Qdrant top ten and PostgreSQL top ten. All returned scores
are joined by ID to independently recomputed PostgreSQL cosine scores. Both lists
must be monotonic within tolerance; substituted IDs must lie within tolerance of
the PostgreSQL cutoff. Equal-score permutations and cutoff substitutions pass.

The operations counter family is required after the isolated write/delete probe.
Both upsert and delete failures are monitored. A declared family with no failure
series has zero failures. The custom runtime registry may not yet have registered
the comparisons family: its absence is recorded as `runtime_comparison_monitor:
not_observed`, never presented as a measured zero. If present, its samples must
parse; a new comparison failure or disappearance of an observed family fails.
The runner's own exact-comparison errors independently fail acceptance. The receipt records runtime and tooling revisions, runner-source hash, sample hash, counts and fixed
parameters without raw IDs, vectors or query text. It is a sampled query-quality
gate, not proof of all-query recall, storage parity or write safety. Promotion also
requires the full storage receipt, write guards, remaining migration checks and
rollback readiness. A successful result does not erase a strict shadow mismatch.
