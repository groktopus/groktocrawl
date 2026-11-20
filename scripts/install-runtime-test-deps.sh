#!/usr/bin/env bash
# Add the hash-locked test tools without changing the already running service's dependencies.
set -euo pipefail

tmp=$(mktemp -d)
trap 'rm -f -- "$tmp"/{before,after}.json "$tmp"/{locked,additions}.txt "$tmp/installed.txt"; rmdir -- "$tmp"' EXIT

snapshot() {
  docker compose exec -T agent-svc python3 - <<'PY'
import json
import re
from importlib.metadata import distributions


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


packages = {}
for dist in distributions():
    name = normalize(dist.metadata["Name"])
    if name in packages and packages[name] != dist.version:
        raise SystemExit(f"multiple installed versions of {name}")
    packages[name] = dist.version
print(json.dumps(packages, sort_keys=True))
PY
}

snapshot > "$tmp/before.json"
export_flags=(
  --locked --only-group fast-tests --no-dev --no-emit-project
  --no-emit-workspace --no-header --no-annotate --format requirements.txt
)
uv export "${export_flags[@]}" --output-file "$tmp/locked.txt" > /dev/null

# pytest and coverage are test-harness tools; an unexpected existing version
# must not silently opt the acceptance run out of its own lock.
python3 - "$tmp/before.json" "$tmp/locked.txt" > "$tmp/installed.txt" <<'PY'
import json
import re
import sys
from pathlib import Path

installed = json.loads(Path(sys.argv[1]).read_text())
pins = dict(re.findall(r"^([a-z0-9-]+)==([^\s;\\]+)", Path(sys.argv[2]).read_text(), re.M))
for name in sorted(installed.keys() & pins.keys()):
    if installed[name] == pins[name]:
        continue
    if name == "coverage" or name == "pytest" or name.startswith("pytest-"):
        raise SystemExit(f"preinstalled test tool {name}=={installed[name]} != lock {pins[name]}")
    print(f"Preserving runtime {name}=={installed[name]} (test lock {pins[name]})", file=sys.stderr)
print("\n".join(sorted(installed)))
PY
exclude=()
while IFS= read -r package; do
  [[ -z "$package" ]] || exclude+=(--no-emit-package "$package")
done < "$tmp/installed.txt"

uv export "${export_flags[@]}" "${exclude[@]}" --output-file "$tmp/additions.txt" > /dev/null
docker compose exec -T agent-svc python3 -m pip install --require-hashes --no-deps -q -r /dev/stdin < "$tmp/additions.txt"
docker compose exec -T agent-svc python3 -m pip check
docker compose exec -T agent-svc python3 -c 'from fastapi import FastAPI; assert FastAPI()'
snapshot > "$tmp/after.json"
python3 - "$tmp/before.json" "$tmp/after.json" <<'PY'
import json
import sys
from pathlib import Path

before, after = (json.loads(Path(path).read_text()) for path in sys.argv[1:])
altered = {name: (version, after.get(name)) for name, version in before.items() if after.get(name) != version}
if altered:
    raise SystemExit(f"test dependency installation changed runtime versions: {altered}")
print(f"Runtime imports passed (FastAPI {after['fastapi']}, Starlette {after['starlette']}); preinstalled versions unchanged")
PY
