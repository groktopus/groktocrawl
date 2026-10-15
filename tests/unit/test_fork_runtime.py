"""Competing-hypothesis forks for imperative and real LangGraph runtimes."""

import importlib.util

import pytest
from agent.experimental.fork_runtime import (
    ForkError,
    ForkJournal,
    ForkPolicy,
    ImperativeForkRuntime,
    build_fork_graph,
)


class Adapter:
    def __init__(self, prefix):
        self.prefix = prefix
        self.calls = []

    def __call__(self, value):
        self.calls.append(value)
        return f"{self.prefix}:{value}"


def test_imperative_fork_reuses_evidence_and_preserves_parent(tmp_path):
    journal = ForkJournal(tmp_path / "journal.json")
    evidence, analyze = Adapter("evidence"), Adapter("analysis")
    runtime = ImperativeForkRuntime(journal, evidence, analyze)
    policy = ForkPolicy()
    parent_at_fork = runtime.start("parent", "scope", policy, "hypothesis-a")
    parent = runtime.finish(parent_at_fork)
    child = runtime.finish(
        runtime.fork(parent_at_fork, "child", policy, "hypothesis-b")
    )

    assert len(evidence.calls) == 1
    assert len(analyze.calls) == 2
    assert parent["hypothesis"] == "hypothesis-a"
    assert child["hypothesis"] == "hypothesis-b"
    assert parent["result_receipt_id"] != child["result_receipt_id"]
    assert journal.run("child")["parent_run_id"] == "parent"


def test_fork_rejects_incompatible_or_unauthorized_reuse(tmp_path):
    journal = ForkJournal(tmp_path / "journal.json")
    policy = ForkPolicy()
    journal.admit("parent", "scope", policy)
    with pytest.raises(ForkError, match="another scope"):
        journal.fork("parent", "foreign", "other-scope", policy)
    with pytest.raises(ForkError, match="incompatible"):
        journal.fork("parent", "changed-model", "scope", ForkPolicy(model_id="model/2"))
    journal.terminal("parent", "deleted")
    with pytest.raises(ForkError, match="deleted"):
        journal.fork("parent", "after-delete", "scope", policy)


def test_parent_deletion_does_not_delete_existing_child(tmp_path):
    journal = ForkJournal(tmp_path / "journal.json")
    evidence, analyze = Adapter("evidence"), Adapter("analysis")
    runtime = ImperativeForkRuntime(journal, evidence, analyze)
    policy = ForkPolicy()
    parent = runtime.start("parent", "scope", policy, "a")
    child = runtime.fork(parent, "child", policy, "b")
    journal.terminal("parent", "deleted")
    completed_child = runtime.finish(child)
    assert completed_child["result_receipt_id"]
    assert journal.run("child")["state"] == "running"


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None
    or importlib.util.find_spec("langgraph.checkpoint.sqlite") is None,
    reason="checkpoint forks are isolated to the optional W4 LangGraph lane",
    owner="repository-maintainer",
    issue="#262",
    classification="retained",
    environment="default test lane excludes optional LangGraph persistence",
)  # type: ignore[call-arg]
def test_real_langgraph_forks_historical_checkpoint_without_mutating_parent(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    journal = ForkJournal(tmp_path / "journal.json")
    evidence, analyze = Adapter("evidence"), Adapter("analysis")
    policy = ForkPolicy()
    journal.admit("parent", "scope", policy)
    config = {"configurable": {"thread_id": "investigation"}}
    with SqliteSaver.from_conn_string(str(tmp_path / "graph.sqlite")) as saver:
        graph = build_fork_graph(journal, evidence, analyze, saver)
        graph.invoke(
            {
                "run_id": "parent",
                "scope_id": "scope",
                "fingerprint": policy.fingerprint,
                "hypothesis": "hypothesis-a",
                "evidence_receipt_id": "",
                "result_receipt_id": "",
            },
            config,
            interrupt_after=["gather"],
        )
        fork_point = graph.get_state(config)
        parent = graph.invoke(None, config)
        parent_config = graph.get_state(config).config

        journal.fork("parent", "child", "scope", policy)
        child_config = graph.update_state(
            fork_point.config,
            {"run_id": "child", "hypothesis": "hypothesis-b", "result_receipt_id": ""},
            as_node="gather",
        )
        child = graph.invoke(None, child_config)
        retained_parent = graph.get_state(parent_config).values
        history_count = len(list(graph.get_state_history(config)))

    assert len(evidence.calls) == 1
    assert len(analyze.calls) == 2
    assert parent["hypothesis"] == retained_parent["hypothesis"] == "hypothesis-a"
    assert child["hypothesis"] == "hypothesis-b"
    assert parent["result_receipt_id"] != child["result_receipt_id"]
    assert history_count >= 6
