"""Admission boundaries before a source storage transaction opens."""

import hashlib
import json

import pytest
from agent.experimental.source_store import MAX_BODY, SourceStore, source_descriptor


def test_descriptor_binds_exact_source_bytes_without_normalizing():
    body = "e\u0301\r\n\0😀".encode()
    result = source_descriptor(body, "https://example.test/a")
    fields = json.loads(result.data)
    assert fields["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert fields["normalization"] == "utf8-exact/1"
    assert (
        result.digest == hashlib.sha256(b"source-staging/1\0" + result.data).hexdigest()
    )
    assert source_descriptor(body.replace(b"\r\n", b"\n"), fields["url"]) != result


@pytest.mark.parametrize(
    "body,url",
    [
        (b"\xff", "https://example.test"),
        (bytearray(b"a"), "https://example.test"),
        (b"x" * (MAX_BODY + 1), "https://example.test"),
        (b"a", "file:///etc/passwd"),
        (b"a", None),
        (b"a", "https://" + "a" * 8193),
    ],
)
def test_bad_sources_rejected_before_database(body, url):
    with pytest.raises(ValueError):
        source_descriptor(body, url)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, -1, True, 1000000000])
async def test_bad_reservation_never_connects(size):
    with pytest.raises(ValueError, match="invalid reservation size"):
        await SourceStore("host=invalid").reserve(None, None, 1, size)
