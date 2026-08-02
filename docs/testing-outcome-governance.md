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

The observed baseline for the current main lineage was **147 skipped, 6
xfailing, and 13 xpassing** in the reported integration run. The issue body
also cites an earlier **147 skipped, 4 xfailed, and 15 xpassed** run. These are
different runs, not interchangeable counts; dependency state and selected
tests changed between them.

This change does not claim that the baseline is fully classified because CI
has not supplied exact xpass node IDs. Strict xpass failures are triaged by
exact node ID and metadata first. Only then may an obsolete xfail marker be
removed and the test classified as `fixed/re-enabled` or `deleted`.

Outcome mapping is:

- `retained`: a justified environment-dependent skip remains necessary.
- `fixed/re-enabled`: the expected failure is fixed and the test runs normally.
- `quarantined`: the test remains an explicitly tracked strict xfail.
- `deleted`: the test or marker is obsolete and removed with its tracking record.
