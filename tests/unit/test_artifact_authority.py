import pytest
from agent.experimental.artifact_authority import (
    MAX_ARTIFACT_BYTES,
    ArtifactAuthority,
    artifact_set_digest,
)


def _artifacts():
    return {
        "summary": ("set-summary", b"summary"),
        "analysis": ("set-analysis", b"analysis"),
        "dossier": ("set-dossier", b"dossier"),
    }


def test_validation_pins_complete_bytes_and_stable_digest():
    manifest_digest, artifacts, digest, total = ArtifactAuthority._validate(
        b"manifest", _artifacts()
    )
    assert len(manifest_digest) == 64
    assert digest == artifact_set_digest(manifest_digest, artifacts)
    assert total == len(b"manifestsummaryanalysisdossier")


def test_validation_rejects_partial_or_oversized_sets():
    with pytest.raises(ValueError, match="complete"):
        ArtifactAuthority._validate(
            b"manifest", {"summary": ("set-summary", b"summary")}
        )
    values = _artifacts()
    values["dossier"] = ("set-dossier", b"x" * (MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(ValueError, match="artifact byte"):
        ArtifactAuthority._validate(b"manifest", values)


@pytest.mark.asyncio
async def test_evidence_admission_bounds_fail_before_storage_connection():
    from uuid import uuid4

    authority = ArtifactAuthority("postgresql://unused.invalid/fixture")
    identity = uuid4()
    with pytest.raises(ValueError, match="knowledge byte"):
        await authority.commit(
            identity,
            identity,
            identity,
            identity,
            b"manifest",
            _artifacts(),
            knowledge=b"x" * (1048576 + 1),
        )
    with pytest.raises(ValueError, match="invalid retained evidence"):
        await authority.commit(
            identity,
            identity,
            identity,
            identity,
            b"manifest",
            _artifacts(),
            evidence={"source-1": (b"x" * (MAX_ARTIFACT_BYTES + 1), "text/plain")},
        )
    with pytest.raises(UnicodeDecodeError):
        await authority.commit(
            identity,
            identity,
            identity,
            identity,
            b"manifest",
            _artifacts(),
            evidence={"source-1": (b"\xff", "text/plain")},
        )
