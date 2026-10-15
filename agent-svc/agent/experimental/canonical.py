"""Bounded JCS byte admission; not a complete Knowledge IR schema or signature."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, NoReturn

import rfc8785

MAX_BYTES = 1_048_576
MAX_DEPTH = 64
MAX_NODES = 100_000
MAX_INTEGER = 2**53 - 1


@dataclass(frozen=True)
class CanonicalDocument:
    schema_version: str
    data: bytes
    digest: str


def _reject_number(_value: str) -> NoReturn:
    raise ValueError("only safe integer JSON numbers are admitted")


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > 16:
        raise ValueError("integer outside safe range")
    result = int(value)
    if abs(result) > MAX_INTEGER:
        raise ValueError("integer outside safe range")
    return result


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _check_nesting(text: str) -> None:
    # Bound nesting before the recursive JSON decoder runs. Syntax is checked by
    # that decoder; escaped quotes/brackets inside strings do not affect depth.
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("JSON nesting limit exceeded")
        elif char in "]}":
            depth -= 1


def _check_values(value: Any) -> None:
    pending = [value]
    count = 0
    while pending:
        current = pending.pop()
        count += 1
        if count > MAX_NODES:
            raise ValueError("JSON node limit exceeded")
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeError:
                raise ValueError("invalid Unicode in JSON") from None
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def admit_canonical_json(raw: bytes, *, schema_version: str) -> CanonicalDocument:
    """Return owned canonical bytes bound to a caller-selected schema version.

    Only numeric integer tokens are accepted; exact decimals belong in schema-
    defined strings. This validates representation, not the document's field schema,
    provenance, permissions, authenticity or semantic truth. No Unicode normalization.
    """
    if not isinstance(raw, bytes):
        raise ValueError("admission requires UTF-8 bytes")
    if len(raw) > MAX_BYTES:
        raise ValueError("JSON byte limit exceeded")
    if (
        not isinstance(schema_version, str)
        or not schema_version
        or len(schema_version) > 200
        or "\0" in schema_version
    ):
        raise ValueError("invalid expected schema version")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("invalid UTF-8 JSON") from None
    _check_nesting(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_int=_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except json.JSONDecodeError:
        raise ValueError("invalid JSON syntax") from None
    if not isinstance(value, dict) or value.get("schema_version") != schema_version:
        raise ValueError("unexpected document schema version")
    _check_values(value)
    canonical = rfc8785.dumps(value)
    if len(canonical) > MAX_BYTES:
        raise ValueError("canonical JSON byte limit exceeded")
    digest = hashlib.sha256(schema_version.encode("utf-8") + b"\0" + canonical)
    return CanonicalDocument(schema_version, canonical, digest.hexdigest())
