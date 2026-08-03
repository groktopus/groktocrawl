"""Anchor tests for issue #470 deliverables (docs + tooling).

Guards the acceptance criteria that the durability contract is explicit in
the public docs, the recovery runbook and tool exist and are indexed, and
the ADR decision record is discoverable. These are content anchors, not a
substitute for prose review.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    path = ROOT / relative
    assert path.exists(), f"missing file: {relative}"
    return path.read_text()


class TestDeploymentCallout:
    def test_deployment_guide_has_durability_section(self):
        text = _read("docs/guides/deployment.md")
        assert "### Job durability and recovery" in text
        assert "not restart-safe" in text
        # SLO boundary is explicit.
        assert "no durability SLO" in text
        # Recovery procedure points at the tool and the runbook.
        assert "reconcile-jobs.py" in text
        assert "interrupted-jobs.md" in text

    def test_readme_quickstart_does_not_imply_durability(self):
        text = _read("README.md")
        assert "best-effort" in text
        assert "not restart-safe" in text
        assert "deployment.md#job-durability-and-recovery" in text


class TestRunbook:
    def test_runbook_exists_and_is_indexed(self):
        runbook = _read("docs/runbooks/interrupted-jobs.md")
        assert "processing" in runbook
        assert "reconcile-jobs.py" in runbook
        index = _read("docs/runbooks/README.md")
        assert "[InterruptedJobs](interrupted-jobs.md)" in index


class TestADRDecisionRecord:
    def test_adr_exists_and_is_indexed(self):
        adr = _read("docs/adr/0047-defer-restart-safe-execution.md")
        assert "Status: accepted" in adr or "* Status: accepted" in adr
        assert "Date: 2026-08-03" in adr
        index = _read("docs/adr/README.md")
        assert "0047" in index
        # Milestones are decomposed into testable increments.
        assert "M1" in adr and "M5" in adr

    def test_cross_links_from_architecture_and_api_guides(self):
        architecture = _read("docs/architecture.md")
        assert "0047-defer-restart-safe-execution.md" in architecture
        assert "interrupted-jobs.md" in architecture
        api = _read("docs/guides/api.md")
        assert "0047-defer-restart-safe-execution.md" in api
        assert "interrupted-jobs.md" in api
