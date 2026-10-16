# Bind coverage to the measured agent copy

CI run [36788582456](https://github.com/magnus919/groktocrawl-x/actions/runs/36788582456)
measured agent sources successfully. Its downloaded `integration-test-coverage`
artifact contains relative keys such as `agent/llm.py` and `agent/routes/agent.py`
in both coverage JSON reports. The container imports the baked `/app/agent` copy;
coverage relativizes it against its working directory. The gate recognized the
absolute Docker spelling but did not recognize this relative spelling. This was
a report-binding defect, rather than absent execution or an import-copy mismatch.

Reprocessing the original reports and exact PR source diff with the corrected
binding yields **35/35 changed executable lines covered**: LLM 28/28, agent route
7/7. The app keyword addition remains correctly informational because it does
not introduce a separately executable statement. The earlier local focused run
covered 34/35; the broader CI lanes covered the remaining line.

Regression fixtures reproduce the container import layout and relative JSON
namespace, and distinguish missing reports from measured comment-only changes.
Missing high-risk modules now fail unless a valid reviewed exception exists;
missing standard-risk modules remain explicitly informational under the existing
risk policy. No thresholds or required checks were relaxed. Raw downloaded
artifacts and local paths are not published. No deployment change is needed.
