"""Future freeze generation uses a reserved provider origin and preserves provenance."""

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_freeze_generator_uses_reserved_origin_without_changing_inherited_provenance(
    tmp_path, monkeypatch
):
    path = Path(__file__).resolve().parents[2] / "scripts/build_w12_mission_freeze.py"
    spec = importlib.util.spec_from_file_location("mission_freeze", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = b"fixture bytes preserved verbatim\n"
    (tmp_path / "fixture.txt").write_bytes(body)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "OUTPUT", tmp_path / "freeze.json")
    monkeypatch.setattr(module, "PINNED_FILES", ("fixture.txt",))
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="a" * 40 + "\n"),
    )
    assert module.main() == 0
    import json

    payload = json.loads((tmp_path / "freeze.json").read_text())
    assert payload["endpoint_origin"] == "https://research-provider.example/"
    assert payload["source_revision"] == "a" * 40
    assert payload["files"] == {"fixture.txt": hashlib.sha256(body).hexdigest()}
    assert payload["inherited_primary_evidence"] == {
        "source": "w12.1-run-v8-a2a743b",
        "file_count": 436,
        "byte_count": 13790342,
        "canonical_manifest_sha256": "46dadbc8d837bcb863257accbe63dc09d7b2f16c400fd00aa58908311e1f518b",
        "copy_policy": "exact read-only copy before any v9 model call",
    }
