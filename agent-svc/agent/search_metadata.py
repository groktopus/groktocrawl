"""Bounded source-reported search metadata shared by every result path."""

import ipaddress
import math
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

STRING_FIELDS = (
    "engine",
    "doi",
    "journal",
    "publisher",
    "editor",
    "pages",
    "number",
    "comments",
    "type",
    "pdf_url",
    "html_url",
    "publishedDate",
)
LIST_FIELDS = ("engines", "authors", "issn", "isbn", "tags")
METADATA_FIELDS = (*STRING_FIELDS, *LIST_FIELDS, "volume", "media")


def search_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """Keep compatible scholarly fields; never pass arbitrary upstream keys."""
    fields: dict[str, Any] = {}
    for key in STRING_FIELDS:
        value = source.get(key)
        if isinstance(value, str) and value and len(value) <= 2048:
            fields[key] = value
    for key in LIST_FIELDS:
        value = source.get(key)
        if (
            isinstance(value, list)
            and len(value) <= 64
            and all(isinstance(item, str) and len(item) <= 256 for item in value)
        ):
            fields[key] = sorted(set(value)) if key == "engines" else list(value)
    if "engines" not in fields and "engine" in fields:
        fields["engines"] = [fields["engine"]]
    volume = source.get("volume")
    if type(volume) in (str, int) and len(str(volume)) <= 256:
        fields["volume"] = volume
    media = media_metadata(source)
    if media:
        fields["media"] = media
    return fields


def _public_media_url(value: Any) -> str | None:
    """Validate metadata only, without DNS, fetches, or exposing signed secrets."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        # Browser URL parsers decode authority escapes before classifying IPs.
        # Reject them here rather than retaining an encoded private-IP alias.
        if "%" in parsed.netloc:
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme not in {"https", "http"}
            or not host
            or parsed.username
            or parsed.password
        ):
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        if "." not in host or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if all(char in "0123456789.xabcdef" for char in host):
                return None
        sensitive = {
            "token",
            "access_token",
            "api_key",
            "apikey",
            "key",
            "signature",
            "sig",
            "password",
            "auth",
            "authorization",
        }
        if any(
            key.lower() in sensitive or key.lower().startswith(("x-amz-", "x-goog-"))
            for key, _ in parse_qsl(parsed.query)
        ):
            return None
    except ValueError:
        return None
    return value


def media_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize SlopSearX MediaInfo and known SearXNG aliases; omit unknowns.

    Dimensions are pixels (1..100000), duration seconds (0..604800). No embed
    HTML, iframe URL, unreported dimension, or inferred media kind is retained.
    """
    nested = source.get("media")
    record = nested if isinstance(nested, dict) else source
    kind = record.get("media_type")
    if not isinstance(kind, str) or kind not in {"image", "video"}:
        kind = {"images": "image", "videos": "video"}.get(
            cast(str, source.get("category"))
            if isinstance(source.get("category"), str)
            else ""
        )
    if kind is None:
        return {}
    result: dict[str, Any] = {"media_type": kind}
    aliases = {
        "url": ("url",)
        if isinstance(nested, dict)
        else ("image_url", "img_src", "video_url"),
        "thumbnail": ("thumbnail", "thumbnail_src"),
        "source": ("source",) if isinstance(nested, dict) else ("source_url", "url"),
    }
    for field, keys in aliases.items():
        for key in keys:
            safe = _public_media_url(record.get(key))
            if safe:
                result[field] = safe
                break
    for field in ("width", "height"):
        value = record.get(field, record.get("image_" + field))
        if type(value) is int and 1 <= value <= 100000:
            result[field] = value
    duration = record.get("duration")
    if (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and 0 <= duration <= 604800
        and math.isfinite(duration)
    ):
        result["duration"] = duration
    return result
