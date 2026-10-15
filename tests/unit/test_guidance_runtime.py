"""Durable human-guidance and restart behavior for the future runtime study."""

import importlib.util

import pytest
from agent.experimental.guidance_runtime import (
    GuidanceError,
    GuidanceJournal,
    ImperativeCheckpointStore,
    ImperativeGuidanceRuntime,
    LangGraphGuidanceRuntime,
    OwnershipToken,
    build_guidance_graph,
)


class FakeOwner:
    def __init__(self):
        self.generation = 0
        self.live = False
        self.terminal = None
        self.checkpoints = []

    def claim(self, run_id, scope_id, owner_id):
        if self.terminal:
            raise GuidanceError(f"run is {self.terminal}")
        if self.live:
            raise GuidanceError("run still has a live owner")
        self.live = True
        self.generation += 1
        return OwnershipToken(run_id, scope_id, owner_id, self.generation)

    def checkpoint(self, token, name, checkpoint_digest):
        assert token.generation == self.generation and self.live
        self.checkpoints.append((name, checkpoint_digest, token.generation))

    def complete(self, token, result_digest):
        assert token.generation == self.generation and self.live
        self.terminal = "completed"
        self.live = False

    def process_lost(self):
        self.live = False


class Research:
    def __init__(self):
        self.calls = 0

    def __call__(self, question):
        self.calls += 1
        return f"evidence for {question}"


class Synthesis:
    def __init__(self):
        self.calls = 0

    def __call__(self, evidence, guidance):
        self.calls += 1
        return f"{evidence}; priority: {guidance}"


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None
    or importlib.util.find_spec("langgraph.checkpoint.sqlite") is None,
    reason="durable LangGraph dependencies are isolated to the optional W4 lane",
    owner="repository-maintainer",
    issue="#260",
    classification="retained",
    environment="default test lane excludes optional LangGraph persistence",
)  # type: ignore[call-arg]
def test_restart_resumes_from_guidance_without_repeating_research(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    journal = GuidanceJournal(tmp_path / "application-journal.json")
    owner = FakeOwner()
    research = Research()
    synthesis = Synthesis()
    checkpoint_path = tmp_path / "langgraph.sqlite"

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        first_graph = build_guidance_graph(journal, research, synthesis, saver)
        pause = LangGraphGuidanceRuntime(first_graph, journal, owner).start(
            "run-1", "scope-1", "worker-before-restart"
        )
        assert pause.question == "Which concern should the research prioritize?"
        assert research.calls == 1
        assert synthesis.calls == 0
        state = first_graph.get_state({"configurable": {"thread_id": "run-1"}})
        encoded = str(state.values)
        assert "evidence for" not in encoded
        assert "priority: reliability" not in encoded

    guidance = journal.record(
        "run-1", "scope-1", "guidance", "answer-1", pause.question, "reliability"
    )
    owner.process_lost()

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        restarted_graph = build_guidance_graph(journal, research, synthesis, saver)
        result = LangGraphGuidanceRuntime(restarted_graph, journal, owner).resume(
            "run-1", "scope-1", "worker-after-restart", guidance.receipt_id
        )

    assert result.output.endswith("priority: reliability")
    assert research.calls == 1
    assert synthesis.calls == 1
    assert owner.generation == 2
    assert owner.terminal == "completed"


def test_imperative_restart_matches_guided_result_without_repeating_research(tmp_path):
    journal = GuidanceJournal(tmp_path / "application-journal.json")
    checkpoint = ImperativeCheckpointStore(tmp_path / "imperative-checkpoint.json")
    owner = FakeOwner()
    research = Research()
    synthesis = Synthesis()
    first_runtime = ImperativeGuidanceRuntime(
        checkpoint, journal, owner, research, synthesis
    )
    pause = first_runtime.start("run-1", "scope-1", "worker-before-restart")
    assert research.calls == 1
    assert synthesis.calls == 0

    guidance = journal.record(
        "run-1", "scope-1", "guidance", "answer-1", pause.question, "reliability"
    )
    owner.process_lost()
    restarted_runtime = ImperativeGuidanceRuntime(
        ImperativeCheckpointStore(tmp_path / "imperative-checkpoint.json"),
        GuidanceJournal(tmp_path / "application-journal.json"),
        owner,
        research,
        synthesis,
    )
    result = restarted_runtime.resume(
        "run-1", "scope-1", "worker-after-restart", guidance.receipt_id
    )
    assert result.output.endswith("priority: reliability")
    assert research.calls == 1
    assert synthesis.calls == 1
    assert owner.generation == 2
    assert owner.terminal == "completed"


def test_process_loss_before_pause_restarts_under_a_new_owner(tmp_path):
    journal = GuidanceJournal(tmp_path / "journal.json")
    checkpoint = ImperativeCheckpointStore(tmp_path / "checkpoint.json")
    owner = FakeOwner()
    owner.claim("run-1", "scope-1", "lost-before-work")
    owner.process_lost()
    research = Research()
    runtime = ImperativeGuidanceRuntime(
        checkpoint, journal, owner, research, Synthesis()
    )
    runtime.start("run-1", "scope-1", "replacement-worker")
    assert research.calls == 1
    assert owner.generation == 2


def test_imperative_resume_rejects_terminal_and_incompatible_runs(tmp_path):
    journal = GuidanceJournal(tmp_path / "journal.json")
    checkpoint = ImperativeCheckpointStore(tmp_path / "checkpoint.json")
    owner = FakeOwner()
    runtime = ImperativeGuidanceRuntime(
        checkpoint, journal, owner, Research(), Synthesis()
    )
    pause = runtime.start("run-1", "scope-1", "worker-1")
    guidance = journal.record(
        "run-1", "scope-1", "guidance", "answer", pause.question, "reliability"
    )
    owner.process_lost()
    owner.terminal = "cancelled"
    with pytest.raises(GuidanceError, match="cancelled"):
        runtime.resume("run-1", "scope-1", "worker-2", guidance.receipt_id)

    owner.terminal = None
    raw = checkpoint.path.read_text().replace(
        "human-guidance/1", "human-guidance/unsupported"
    )
    checkpoint.path.write_text(raw)
    with pytest.raises(GuidanceError, match="unsupported"):
        runtime.resume("run-1", "scope-1", "worker-2", guidance.receipt_id)
    assert owner.generation == 1


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None
    or importlib.util.find_spec("langgraph.checkpoint.sqlite") is None,
    reason="durable LangGraph dependencies are isolated to the optional W4 lane",
    owner="repository-maintainer",
    issue="#260",
    classification="retained",
    environment="default test lane excludes optional LangGraph persistence",
)  # type: ignore[call-arg]
def test_langgraph_recovers_checkpoint_after_guidance_acceptance(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    journal = GuidanceJournal(tmp_path / "journal.json")
    owner = FakeOwner()
    research = Research()
    synthesis = Synthesis()
    path = tmp_path / "graph.sqlite"
    config = {"configurable": {"thread_id": "run-1"}}
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_guidance_graph(journal, research, synthesis, saver)
        pause = LangGraphGuidanceRuntime(graph, journal, owner).start(
            "run-1", "scope-1", "worker-1"
        )
        guidance = journal.record(
            "run-1", "scope-1", "guidance", "answer", pause.question, "reliability"
        )
        graph.update_state(
            config, {"guidance_receipt_id": guidance.receipt_id}, as_node="ask"
        )
    owner.process_lost()

    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_guidance_graph(journal, research, synthesis, saver)
        result = LangGraphGuidanceRuntime(graph, journal, owner).recover_after_guidance(
            "run-1", "scope-1", "worker-2"
        )
    assert result.output.endswith("priority: reliability")
    assert research.calls == 1
    assert synthesis.calls == 1
    assert owner.generation == 2


def test_guidance_journal_rejects_foreign_scope_and_conflicting_replay(tmp_path):
    journal = GuidanceJournal(tmp_path / "journal.json")
    journal.initialize("run-1", "scope-1")
    first = journal.record(
        "run-1", "scope-1", "guidance", "answer", "question", "reliability"
    )
    assert (
        journal.record(
            "run-1", "scope-1", "guidance", "answer", "question", "reliability"
        )
        == first
    )
    with pytest.raises(GuidanceError, match="another scope"):
        journal.get("run-1", "scope-2", first.receipt_id)
    with pytest.raises(GuidanceError, match="conflicts"):
        journal.record(
            "run-1", "scope-1", "guidance", "answer", "question", "speed"
        )
