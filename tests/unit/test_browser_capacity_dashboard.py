"""Offline structural contracts; not Grafana/Prometheus semantic validation."""

import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "browser_dashboard",
    Path(__file__).parents[2] / "scripts/extend_browser_capacity_dashboard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source():
    return {
        "uid": "existing-dashboard",
        "version": 8,
        "schemaVersion": 39,
        "refresh": "30s",
        "templating": {"list": []},
        "time": {"from": "now-6h", "to": "now"},
        "panels": [
            {
                "id": 4,
                "title": "Original",
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
                "panels": [{"id": 99, "gridPos": {"x": 0, "y": 8, "w": 12, "h": 8}}],
            }
        ],
    }


def render(value=None):
    return MODULE.extend_dashboard(
        value or source(),
        datasource_uid="prom-test",
        job="agent-test",
        instance="node-test",
    )


def test_preserves_original_and_allocates_nonoverlapping_ids():
    original = source()
    snapshot = copy.deepcopy(original)
    result = render(original)
    assert original == snapshot
    assert {k: v for k, v in result.items() if k != "panels"} == {
        k: v for k, v in original.items() if k != "panels"
    }
    assert result["panels"][:1] == original["panels"]
    added = result["panels"][1:]
    assert len(added) == 10
    assert len({p["id"] for p in added}) == 10
    assert min(p["id"] for p in added) > 99
    assert min(p["gridPos"]["y"] for p in added) >= 16


def test_queries_keep_target_scope_nulls_and_counter_semantics():
    panels = render()["panels"][2:]
    queries = [target["expr"] for panel in panels for target in panel["targets"]]
    assert all('job="agent-test",instance="node-test"' in query for query in queries)
    assert all("or vector(0)" not in query for query in queries)
    assert all("sum(" not in query for query in queries)
    assert any(
        "changes(groktocrawl_browser_service_start_time_seconds" in q for q in queries
    )
    assert any(
        "resets(groktocrawl_browser_cgroup_limit_events_total" in q for q in queries
    )
    assert any(q.startswith("time() - (") and q.endswith(" > 0)") for q in queries)
    for panel in panels:
        assert not panel["fieldConfig"]["defaults"]["custom"]["spanNulls"]
        assert "thresholds" not in panel["fieldConfig"]["defaults"]
        assert "alert" not in panel
        assert panel["datasource"]["uid"] == "prom-test"
        targets = panel["targets"]
        assert len({target["refId"] for target in targets}) == len(targets)
        for target in targets:
            query = target["expr"]
            if "_total{" in query:
                assert query.startswith(("increase(", "resets("))
                assert "[$__rate_interval]" in query


def test_refuses_duplicate_row_and_empty_scope():
    with pytest.raises(ValueError, match="already exists"):
        render(render())
    with pytest.raises(ValueError, match="nonempty"):
        MODULE.extend_dashboard(
            source(), datasource_uid="prom", job="", instance="node"
        )


def test_promql_label_values_are_escaped():
    result = MODULE.extend_dashboard(
        source(), datasource_uid="prom", job='a"b', instance="line\nnext"
    )
    query = result["panels"][2]["targets"][0]["expr"]
    assert 'job="a\\"b"' in query
    assert 'instance="line\\nnext"' in query


def test_cli_never_overwrites_source_or_existing_output(tmp_path):
    import json
    import subprocess
    import sys

    original = tmp_path / "source.json"
    candidate = tmp_path / "candidate.json"
    original.write_text(json.dumps(source()))
    before = original.read_bytes()
    command = [
        sys.executable,
        str(SPEC.origin),
        str(original),
        str(candidate),
        "--datasource-uid",
        "prom-test",
        "--job",
        "agent-test",
        "--instance",
        "node-test",
    ]
    first = subprocess.run(command, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    rendered = candidate.read_bytes()
    repeated = subprocess.run(command, capture_output=True, check=False)
    assert repeated.returncode != 0
    assert candidate.read_bytes() == rendered
    command[3] = str(original)
    same_path = subprocess.run(command, capture_output=True, check=False)
    assert same_path.returncode != 0
    assert original.read_bytes() == before
