"""Strict publication-date contract for audited scholarly search."""

import re
from datetime import date
from typing import Any


def validate_dates(
    date_from: str | None,
    date_to: str | None,
    time_range: str | None,
    engines: list[str] | None,
) -> bool:
    bounded = date_from is not None or date_to is not None or time_range is not None
    if not bounded:
        return False
    for value in (date_from, date_to):
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
            ):
                raise ValueError("Date bounds require YYYY-MM-DD")
            date.fromisoformat(value)
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from must not exceed date_to")
    if time_range is not None:
        raise ValueError("time_range is not supported; use explicit date_from/date_to")
    if engines != ["openalex"]:
        raise ValueError("Date bounds require explicit engines=['openalex']")
    return True


def verify_date_proof(proof: Any, date_from: str | None, date_to: str | None) -> dict:
    expected = {"date_from": date_from, "date_to": date_to}
    if not isinstance(proof, dict) or (
        proof.get("requested") != {**expected, "time_range": None}
        or proof.get("applied") != expected
        or proof.get("status") != "enforced"
        or proof.get("engines") != ["openalex"]
        or proof.get("enforced_by") != ["upstream:openalex"]
        or not isinstance(proof.get("warnings"), list)
        or not isinstance(proof.get("reason"), str)
    ):
        raise ValueError("Search backend did not prove the requested date enforcement")
    return proof
