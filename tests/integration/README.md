# Integration Test Policy

GitHub adapter contract gates are deterministic and live in
`tests/unit/test_github_adapter_contract.py`. They exercise `GitHubAdapter`
with checked-in fixtures and never contact GitHub or the scraper service.

`test_github_adapter_external.py` contains the separate `external`-marked
compatibility probe. It calls the deployed scraper service with a bounded
timeout and is an external signal, not a deterministic adapter gate. Default
pytest runs exclude external probes. Run them explicitly with `pytest -m external`;
external failures retain observed status, body, and exception diagnostics.
