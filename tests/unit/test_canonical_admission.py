"""ADR-0071 admission boundaries and RFC 8785 restricted-domain vectors."""

import hashlib
import json

import pytest
from agent.experimental.canonical import (
    MAX_BYTES,
    MAX_DEPTH,
    MAX_INTEGER,
    MAX_NODES,
    admit_canonical_json,
)

VERSION = "storage-envelope-prototype/1"


def raw_document(value):
    return json.dumps(
        {"schema_version": VERSION, "value": value}, ensure_ascii=True
    ).encode()


def admit(raw):
    return admit_canonical_json(raw, schema_version=VERSION)


def test_canonical_order_roundtrip_and_digest():
    raw = b'{ "value": {"z":true,"a":[null,false,-0,42]}, "schema_version":"storage-envelope-prototype/1"}'
    result = admit(raw)
    expected = b'{"schema_version":"storage-envelope-prototype/1","value":{"a":[null,false,0,42],"z":true}}'
    assert result.data == expected
    assert (
        result.digest == hashlib.sha256(VERSION.encode() + b"\0" + expected).hexdigest()
    )
    assert admit(result.data) == result
    assert admit(raw_document({"a": [None, False, 0, 42], "z": True})) == result


def test_rfc_utf16_property_sorting():
    # RFC 8785 section 3.2.3 ordering vector, including supplementary U+1F600.
    keys = ["\ufb33", "😀", "€", "ö", "\u0080", "1", "\r"]
    result = admit(raw_document(dict.fromkeys(keys, 1)))
    assert list(json.loads(result.data)["value"]) == [
        "\r",
        "1",
        "\u0080",
        "ö",
        "€",
        "😀",
        "\ufb33",
    ]
    assert result.data.index("😀".encode()) < result.data.index("\ufb33".encode())


def test_strings_preserve_unicode_and_escape_controls():
    result = admit(raw_document(["é", "e\u0301", '\b\t\n\f\r\x00"\\/']))
    assert b'\\b\\t\\n\\f\\r\\u0000\\"\\\\/' in result.data
    assert json.loads(result.data)["value"][:2] == ["é", "e\u0301"]
    assert admit(raw_document("é")).digest != admit(raw_document("e\u0301")).digest


@pytest.mark.parametrize("value", [MAX_INTEGER, -MAX_INTEGER, 0, True, None])
def test_safe_values(value):
    assert json.loads(admit(raw_document(value)).data)["value"] == value


@pytest.mark.parametrize(
    "token",
    [
        "1.0",
        "1e0",
        "1e999",
        "NaN",
        "Infinity",
        "-Infinity",
        str(MAX_INTEGER + 1),
        str(-MAX_INTEGER - 1),
    ],
)
def test_unsupported_numbers(token):
    with pytest.raises(ValueError):
        admit(
            b'{"schema_version":"'
            + VERSION.encode()
            + b'","value":'
            + token.encode()
            + b"}"
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"storage-envelope-prototype/1","x":1,"x":2}',
        b'{"schema_version":"storage-envelope-prototype/1","value":{"x":1,"\\u0078":2}}',
        raw_document("\ud800"),
        raw_document({"\udfff": 1}),
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{} garbage",
        b"[]",
        b"{}",
        raw_document(1) + b"{}",
    ],
)
def test_ambiguous_or_invalid_inputs(raw):
    with pytest.raises(ValueError):
        admit(raw)


def test_schema_binding_and_bytes_only():
    with pytest.raises(ValueError, match="schema"):
        admit_canonical_json(raw_document(1), schema_version="other/1")
    with pytest.raises(ValueError, match="bytes"):
        admit("{}")
    for version in ("", "x\0y", "x" * 201):
        with pytest.raises(ValueError):
            admit_canonical_json(b"{}", schema_version=version)


def test_size_depth_and_nodes():
    with pytest.raises(ValueError, match="byte limit"):
        admit(b" " * (MAX_BYTES + 1))
    prefix = b'{"schema_version":"' + VERSION.encode() + b'","value":'
    assert admit(prefix + b"[" * (MAX_DEPTH - 1) + b"0" + b"]" * (MAX_DEPTH - 1) + b"}")
    with pytest.raises(ValueError, match="nesting"):
        admit(prefix + b"[" * MAX_DEPTH + b"0" + b"]" * MAX_DEPTH + b"}")
    with pytest.raises(ValueError, match="node"):
        admit(raw_document([0] * MAX_NODES))
    assert admit(raw_document("[" * 100 + '\\"' + "]" * 100))


def test_pinned_serializer_rfc_number_vector():
    # RFC 8785 section 3.2.2: serializer capability, not admitted numeric fields.
    import rfc8785

    assert rfc8785.dumps([333333333.33333329, 1e30, 4.50, 2e-3, 1e-27]) == (
        b"[333333333.3333333,1e+30,4.5,0.002,1e-27]"
    )
