ALTER TABLE research_staging.research_artifact_sets ADD COLUMN objective text CHECK(objective IS NULL OR length(objective)<=10000);
ALTER TABLE research_staging.research_artifact_sets ADD COLUMN knowledge bytea CHECK(knowledge IS NULL OR octet_length(knowledge)<=1048576);
ALTER TABLE research_staging.research_artifact_sets ADD COLUMN knowledge_digest text CHECK(knowledge_digest IS NULL OR length(knowledge_digest)=64);
-- Exact citation material shares artifact authority retention and tombstones.
CREATE TABLE research_staging.research_artifact_evidence (
 scope_id uuid NOT NULL,
 research_id uuid NOT NULL,
 snapshot_id text NOT NULL CHECK(length(snapshot_id) BETWEEN 1 AND 200),
 body bytea NOT NULL CHECK(octet_length(body)<=10485760),
 content_digest text NOT NULL CHECK(length(content_digest)=64),
 media_type text NOT NULL CHECK(media_type IN ('text/plain','text/markdown')),
 PRIMARY KEY(scope_id,research_id,snapshot_id),
 FOREIGN KEY(scope_id,research_id) REFERENCES research_staging.research_artifact_sets ON DELETE CASCADE
);
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK(version IN(1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16));
UPDATE research_staging.schema_version SET version=16;
