"""Contract tests for public GroktoCrawl observability source artifacts."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = {
    "agent-svc": ROOT / "docs/grafana/agent-svc-dashboard.json",
    "scraper-svc": ROOT / "docs/grafana/scraper-svc-dashboard.json",
    "semantic-svc": ROOT / "docs/grafana/semantic-svc-dashboard.json",
}
RUNBOOK_URL = "https://github.com/groktopus/groktocrawl/blob/main/"


class ObservabilityContractTests(unittest.TestCase):
    def test_dashboards_are_portable_and_owned(self) -> None:
        for service, path in DASHBOARDS.items():
            with self.subTest(dashboard=path.name):
                dashboard = json.loads(path.read_text())
                self.assertIn("owner:groktocrawl-maintainers", dashboard["tags"])
                self.assertIn("Owner: GroktoCrawl maintainers", dashboard["description"])
                self.assertEqual(dashboard["refresh"], "30s")
                self.assertEqual(dashboard["time"], {"from": "now-6h", "to": "now"})

                variables = dashboard["templating"]["list"]
                self.assertTrue(
                    any(
                        variable.get("name") == "datasource"
                        and variable.get("type") == "datasource"
                        and variable.get("query") == "prometheus"
                        for variable in variables
                    )
                )

                for panel in dashboard["panels"]:
                    self.assertEqual(panel["datasource"]["uid"], "$datasource")
                    self.assertNotEqual(
                        panel.get("fieldConfig", {})
                        .get("defaults", {})
                        .get("color", {})
                        .get("mode"),
                        "background",
                    )
                    for target in panel.get("targets", []):
                        expression = target["expr"]
                        self.assertIn(f'job="{service}"', expression)
                        self.assertNotIn("instance=", expression)

    def test_scrape_fragment_has_only_owned_service_jobs(self) -> None:
        fragment = (ROOT / "docs/prometheus/scrape-config.yml").read_text()
        job_names = re.findall(r"^  - job_name: ([\w-]+)$", fragment, re.MULTILINE)
        self.assertEqual(job_names, ["agent-svc", "scraper-svc", "semantic-svc"])
        for service in job_names:
            self.assertRegex(
                fragment,
                rf"job_name: {service}[\s\S]*?owner: groktocrawl-maintainers"
                rf"[\s\S]*?service: {service}",
            )

    def test_alerts_have_owner_and_existing_runbooks(self) -> None:
        alerts = (ROOT / "docs/prometheus/alerts.yml").read_text()
        blocks = re.split(r"(?m)^      - alert: ", alerts)[1:]
        self.assertEqual(len(blocks), 3)

        for block in blocks:
            alert_name = block.splitlines()[0]
            with self.subTest(alert=alert_name):
                self.assertIn("owner: groktocrawl-maintainers", block)
                match = re.search(r'runbook_url: "([^"]+)"', block)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertTrue(match.group(1).startswith(RUNBOOK_URL))
                runbook_path = match.group(1).removeprefix(RUNBOOK_URL)
                self.assertTrue((ROOT / runbook_path).is_file())


if __name__ == "__main__":
    unittest.main()
