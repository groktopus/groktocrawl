# Bounded source-store restore rehearsal

This is a required CI rehearsal for the experimental source-staging schema,
before extending retained storage to research revisions. It is not an operator
restore command, production migration, recovery SLO or completed W3 gate.
It follows [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md)
and uses the actual [SourceStore adapter](research-source-storage.md).

## What runs

After the eleven application storage contracts, the isolated PostgreSQL CI job:

1. Creates two fresh source roots with synthetic bytes, one to retain and one to
   delete later. Existing valid application test records are also in the corpus.
2. Runs PostgreSQL 17 `pg_dump` of the isolated database into a private temporary
   file. No global roles, ownership commands or privilege grants are exported.
3. Deletes one fixture root **after** the dump and captures the current root
   deletion inventory into a separate file that is not part of that backup.
4. Creates a new `research_restore_probe` database in the same private test cluster
   and restores the SQL dump transactionally with errors treated as failures.
5. Proves the restored, pre-deletion source still exists. Missing or incomplete
   deletion inventory must reject verification before any normal access decision.
6. Applies the current deletion inventory through `SourceStore.delete_root`.
   Verifies that the subsequently deleted source is unavailable and that the
   retained control source still opens with its exact bytes. It also revalidates
   every live source's canonical descriptor/body references and checks that every
   committed receipt on a live root still resolves to its snapshot.

The candidate database has no public serving endpoint and stays inside the
isolated Compose network. A successful SQL restore alone never opens access.
The inventory verifier uses a known synthetic deletion as its oracle; it is not a
general authenticated deletion-inventory protocol for operator use. It cannot
reconstruct unknown later deletions from an older backup. A real restore without
trusted current deletion history must remain quarantined.

The existing corruption test explicitly deletes its deliberately damaged root
after proving read rejection, so the remaining database corpus is valid for this
rehearsal. Cleanup does not turn corrupted content into valid content.

## Evidence and limits

`tests/storage/restore_source_store.py` is an explicitly invoked test helper,
with `restore-seed`, `restore-delete` and `restore-verify` phases. The existing
`probe.sh` prepares its private connection credential. The workflow records
PostgreSQL version, verified source count, deletion inventory count, receipt
resolution and the post-backup deletion result. It uploads `restore-result.json`
as `source-storage-restore-result`. The job summary records dump/restore elapsed
whole seconds and backup bytes. The dump and deletion inventory are private
runner-temporary files and are not uploaded. Database volumes remain intact until
the disposable runner is discarded.

This proves one bounded schema-1 logical dump/restore path, not a measured pilot
RPO/RTO. It does not exercise lost WAL, point-in-time recovery, a cluster upgrade,
physical disk loss, permission migration, independently authenticated deletion
inventories, secret stripping from arbitrary future schemas or large-corpus
capacity. The two databases share one isolated cluster; this does not establish
recovery from loss of that cluster. IR/render reference closure, retained schema
readers and future migration compatibility need their own tests when implemented.

The reproduction sequence is checked into the `Rehearse backup restore and
post-backup deletion` step of [Runtime CI](../../.github/workflows/runtime.yml).
Run it only with the explicitly selected isolated harness, after a fresh adapter
test run. Creation refuses an existing restore database. No existing database or
volume is dropped to make the test pass.

Source: [PostgreSQL SQL dump backup and restore](https://www.postgresql.org/docs/17/backup-dump.html).

The [retained publication extension](research-retained-publications.md) runs this
rehearsal on schema 3, including retained/deleted published controls, exact output
reopening and live publication receipt closure. It also restores the pre-migration
schema-2 dump into a separate new database and compares source/revision manifests.
These extensions preserve the same isolated-fixture and recovery-evidence limits.

Schema 4 additionally rehearses historical re-rendering: both original and new
presentations participate in retained/deleted controls, and pre-migration schema-3
source/revision/publication manifests must match the separately restored backup.
Fixture contexts come from a fixed trusted allowlist, never arbitrary payload policy.

[Bounded artifact bundles](research-artifact-bundles.md) now support consistent
export and offline integrity validation with preserved original identities.
Recipient scope mapping and atomic import remain unimplemented.
