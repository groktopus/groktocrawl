"""Deterministic capacity corpus and descriptive measurement helpers."""

import hashlib
import json
import math
import statistics

DESIGN = "retained-storage-capacity/1"
GENERATOR = "sha256-counter-ascii-hex/1"
SEED = "groktocrawl-x-capacity-v1"
MIB = 1024 * 1024
BODY_SIZE = 9 * MIB


def body_for(phase, root, source, size=BODY_SIZE):
    """Unique counter blocks; one bounded bytearray, no corpus-sized allocation."""
    prefix = f"{SEED}:{phase}:{root}:{source}:".encode()
    body = bytearray()
    for counter in range((size + 63) // 64):
        body.extend(hashlib.sha256(prefix + str(counter).encode()).hexdigest().encode())
    del body[size:]
    return bytes(body)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def summaries(events):
    groups = {}
    for item in events:
        if item.get("event") != "operation":
            continue
        key = f"{item['phase']}:{item['kind']}"
        groups.setdefault(key, []).append(item)
    result = {}
    for key, items in groups.items():
        values = sorted(item["duration_seconds"] for item in items)
        result[key] = {
            "attempted": len(items),
            "succeeded": sum(item["outcome"] == "success" for item in items),
            "failed": sum(item["outcome"] != "success" for item in items),
            "minimum_seconds": values[0],
            "median_seconds": statistics.median(values),
            "nearest_rank_p95_seconds": values[math.ceil(0.95 * len(values)) - 1],
            "maximum_seconds": values[-1],
        }
    return result


def format_boundary_payload(raw):
    """Valid Text character counts, large UTF-8 bytes below publication admission."""
    data = json.loads(raw)
    for audit in data["publication"]["audits"]:
        audit["reason"] = "Synthetic format boundary. " + "🧪" * 70000
    return json.dumps(data, ensure_ascii=False).encode()


def adapter_identity(raw):
    """Require the observed container image and effective declared resource limits."""
    value = json.loads(raw)
    image = value.get("image_id", "")
    if (
        not isinstance(image, str)
        or not image.startswith("sha256:")
        or len(image) != 71
    ):
        raise ValueError("missing adapter image identity")
    if any(char not in "0123456789abcdef" for char in image[7:]):
        raise ValueError("invalid adapter image identity")
    if (
        value.get("memory_bytes") != 256 * MIB
        or value.get("nano_cpus") != 1_000_000_000
    ):
        raise ValueError("adapter limits differ from the declared probe")
    return value
