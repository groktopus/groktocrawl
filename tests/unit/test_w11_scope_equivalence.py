from typing import Any

import pytest

from scripts.capture_w11_scope_equivalence import capture


class FakeClient:
    def __init__(self, explicit: list[str] | None = None) -> None:
        self.explicit = explicit
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if "categories" in arguments:
            return {"selected_engines": ["a", "b"]}
        return {"selected_engines": self.explicit or ["a", "b"]}


def test_capture_proves_category_and_explicit_scopes_match() -> None:
    client = FakeClient()
    result = capture(client, query="probe")
    assert result["scope_equal"] is True
    assert result["dispatches"] == 0
    assert result["http_control"]["engines"] == ["a", "b"]
    assert client.calls[1][1]["engines"] == ["a", "b"]


def test_capture_rejects_scope_drift() -> None:
    with pytest.raises(ValueError, match="does not reproduce"):
        capture(FakeClient(["a"]), query="probe")
