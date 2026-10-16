#!/usr/bin/env python3
"""Fail closed when replacement-candidate credentials use volatile storage."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

VOLATILE_ROOTS = tuple(
    path.resolve() for path in (Path("/tmp"), Path("/private/tmp"), Path("/var/tmp"))
)
REQUIRED_PRIVATE_VALUES = ("CANDIDATE_API_KEY", "LLM_API_KEY")


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_private_file(path: Path, label: str) -> None:
    resolved = path.expanduser().resolve()
    if not path.is_absolute():
        raise ValueError(f"{label} must use an absolute path")
    if any(_under(resolved, root) for root in VOLATILE_ROOTS):
        raise ValueError(f"{label} must not be stored in volatile temporary storage")
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist or is not a regular file")
    if resolved.stat().st_mode & 0o077:
        raise ValueError(f"{label} must not be readable or writable by group or others")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def validate(env_file: Path) -> None:
    _validate_private_file(env_file, "candidate environment file")
    values = _read_env(env_file)

    revision = values.get("CANDIDATE_IMAGE_TAG", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("CANDIDATE_IMAGE_TAG must be a full Git revision")

    for key in REQUIRED_PRIVATE_VALUES:
        value = values.get(key, "")
        if not value or value.startswith("replace-with-"):
            raise ValueError(f"{key} must be set to a private value")

    password_file = values.get("CANDIDATE_POSTGRES_PASSWORD_FILE", "")
    if not password_file:
        raise ValueError("CANDIDATE_POSTGRES_PASSWORD_FILE must be set")
    _validate_private_file(Path(password_file), "PostgreSQL password file")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate durable, private replacement-candidate configuration."
    )
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    try:
        validate(args.env_file)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("candidate configuration is durable and private")
    return os.EX_OK


if __name__ == "__main__":
    raise SystemExit(main())
