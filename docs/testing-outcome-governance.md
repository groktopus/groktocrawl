# Test Outcome Governance

## Metadata

Every root `tests/` skip, skip condition, and expected failure carries these
fields:

```python
pytest.mark.xfail(
    strict=True,
    reason="why this is currently expected",
    owner="repository-maintainer",
    issue="#502",
    classification="quarantined",
    environment="the explicit condition that justifies it",
)
```

Runtime conditions use `tests.outcome_governance.governed_skip` with the same
fields. `classification` is one of `retained`, `fixed/re-enabled`,
`quarantined`, or `deleted`. Use `retained` for environment-dependent skips and
`quarantined` for current xfails. A review date may replace `environment` when
the condition is not environment-dependent.

## Reports

The pytest hooks write `test-outcomes.json` and `test-outcomes.md` locally.
Set `QA_OUTCOME_PATH` to choose the JSON path; the Markdown report uses the
same stem. For example:

```bash
PYTHONPATH=agent-svc:scraper-svc:llm-svc:parse-svc:portal-svc:browser-svc:semantic-svc:. \
QA_OUTCOME_PATH=artifacts/qa-outcomes.json \
python -m pytest tests/unit/ tests/service/ --no-cov
```

Fast Tests uploads `fast-test-outcomes`. Docker Integration uploads
`integration-test-outcomes`. Both workflows also publish the Markdown report
to the GitHub Actions Job Summary.

## Baseline and Classification

The issue body cites an earlier **147 skipped, 4 xfailed, and 15 xpassed** run.
A later exact-head run on PR #509 at `41d13c5` selected a different set and
reported **951 passed, 144 skipped, 6 xfailed, 13 xpassed, and 2 setup
errors**. These are different runs, not interchangeable counts. The exact
node IDs from the later run are the authoritative input for this triage.

The 13 xpasses were classified as `fixed/re-enabled` and their stale strict
xfail markers were removed:

- `tests/integration/test_stack.py::test_scraper_uses_accept_markdown`
- `tests/integration/test_stack.py::test_mitreattack_adapter_technique`
- `tests/integration/test_stack.py::test_virustotal_adapter_file`
- `tests/integration/test_stack.py::test_index_structure`
- `tests/integration/test_stack.py::test_near_dup_detection_skip_mode`
- `tests/integration/test_stack.py::test_near_dup_detection_update_mode`
- `tests/integration/test_stack.py::test_near_dup_different_content`
- `tests/integration/test_stack.py::test_batch_index_endpoint`
- `tests/integration/test_stack.py::test_batch_index_empty`
- `tests/integration/test_stack.py::test_gutenberg_adapter_known_book`
- `tests/integration/test_stack.py::test_gutenberg_adapter_files_url`
- `tests/integration/test_stack.py::test_gutenberg_adapter_cache_url`
- `tests/integration/test_stack.py::test_gutenberg_adapter_invalid_id`

The remaining seven declared strict xfails are `quarantined` and retain
explicit owner, issue, reason, and environment metadata. The two setup errors
were caused by passing governance-only keyword arguments into pytest's native
skip markers; the governance hook now validates and retains those fields on
the collected item, then strips only the native-marker-incompatible fields
before pytest evaluates the marker. Shared class-level markers are sanitized
only after all collected items have been validated.

Outcome mapping is:

- `retained`: a justified environment-dependent skip remains necessary.
- `fixed/re-enabled`: the expected failure is fixed and the test runs normally.
- `quarantined`: the test remains an explicitly tracked strict xfail.
- `deleted`: the test or marker is obsolete and removed with its tracking record.
