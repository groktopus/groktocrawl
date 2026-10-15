"""Host-side bounded Compose experiment. Never remove databases or volumes."""

import json
import os
import platform
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from capacity_common import DESIGN, adapter_identity, digest, summaries


class Runner:
    def __init__(self):
        self.out = Path("capacity-results")
        self.out.mkdir(exist_ok=False)
        self.deadline = time.monotonic() + 1200
        self.events = []
        self.stop = threading.Event()
        self.report = {
            "schema_version": "retained-storage-capacity-result/1",
            "status": "incomplete",
            "design": DESIGN,
            "utc_start": datetime.now(UTC).isoformat(),
            "sampling_interval_seconds": 2,
            "sampling_failures": 0,
        }
        self.container = os.environ["COMPOSE_PROJECT_NAME"] + "-capacity"

    def command(self, args, *, stdin=None, stdout=None):
        return subprocess.run(
            args,
            stdin=stdin,
            stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=max(0.001, self.deadline - time.monotonic()),
        )

    def compose(self, args, **kwargs):
        return self.command(["docker", "compose", *args], **kwargs)

    def db(self, database, args, **kwargs):
        return self.compose(
            [
                "exec",
                "-T",
                "research-postgres",
                *args,
                "-U",
                "research_owner",
                "-d",
                database,
            ],
            **kwargs,
        )

    def sample(self):
        with (self.out / "resources.jsonl").open("w") as target:
            while not self.stop.is_set():
                record = {"monotonic": time.monotonic(), "containers": []}
                try:
                    ids = subprocess.run(
                        [
                            "docker",
                            "ps",
                            "--filter",
                            "label=com.docker.compose.project="
                            + os.environ["COMPOSE_PROJECT_NAME"],
                            "--format",
                            "{{.ID}}",
                        ],
                        capture_output=True,
                        check=True,
                        text=True,
                        timeout=5,
                    ).stdout.split()
                    if not ids:
                        raise RuntimeError("no project containers")
                    observed = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            '{"image_id":{{json .Image}},"memory_bytes":{{json .HostConfig.Memory}},"nano_cpus":{{json .HostConfig.NanoCpus}}}',
                            self.container,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    if observed.returncode == 0:
                        try:
                            identity = adapter_identity(observed.stdout)
                        except ValueError:
                            self.report["adapter_identity_invalid"] = True
                            raise
                        record["adapter_identity"] = identity
                        previous = self.report.get("adapter_identity")
                        if previous is not None and identity != previous:
                            self.report["adapter_identity_changed"] = True
                        self.report["adapter_identity"] = identity
                    stats = subprocess.run(
                        [
                            "docker",
                            "stats",
                            "--no-stream",
                            "--format",
                            "{{json .}}",
                            *ids,
                        ],
                        capture_output=True,
                        check=True,
                        text=True,
                        timeout=5,
                    )
                    record["containers"] = [
                        json.loads(line) for line in stats.stdout.splitlines()
                    ]
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    self.report["sampling_failures"] += 1
                target.write(json.dumps(record) + "\n")
                target.flush()
                self.stop.wait(2)

    def phase(self, name, database, stdin=None):
        with (self.out / f"{name}.jsonl").open("wb") as target:
            try:
                self.compose(
                    [
                        "run",
                        "--rm",
                        "-T",
                        "--name",
                        self.container,
                        "-e",
                        "PGDATABASE=" + database,
                        "storage-adapter",
                        "capacity-" + name.split("-")[0],
                    ],
                    stdin=stdin,
                    stdout=target,
                )
            except subprocess.CalledProcessError as exc:
                (self.out / f"{name}-stderr.log").write_bytes(exc.stderr)
                raise

    def timing(self, name, call):
        start = time.monotonic()
        outcome = "success"
        try:
            return call()
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            end = time.monotonic()
            self.events.append(
                {
                    "event": "operation",
                    "phase": "backup",
                    "kind": name,
                    "start": start,
                    "end": end,
                    "duration_seconds": end - start,
                    "outcome": outcome,
                    "byte_count": 0,
                }
            )

    def run(self):
        thread = threading.Thread(target=self.sample, daemon=True)
        try:
            self.report["code_commit"] = (
                self.command(["git", "rev-parse", "HEAD"]).stdout.decode().strip()
            )
            self.report["design_digest"] = digest(
                Path("docs/experiments/research-storage-capacity.md").read_bytes()
            )
            self.report["host"] = {
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "memory_bytes": os.sysconf("SC_PAGE_SIZE")
                * os.sysconf("SC_PHYS_PAGES"),
            }
            dbid = (
                self.compose(["ps", "-q", "research-postgres"]).stdout.decode().strip()
            )
            # Select only identity/resource fields, never container environment.
            template = "{{json .Image}} {{json .HostConfig.Memory}} {{json .HostConfig.NanoCpus}}"
            self.report["postgres_image_and_limits"] = (
                self.command(["docker", "inspect", "--format", template, dbid])
                .stdout.decode()
                .strip()
            )
            config = json.loads(self.compose(["config", "--format", "json"]).stdout)
            self.report["adapter_limits"] = {
                key: config["services"]["storage-adapter"].get(key)
                for key in ("mem_limit", "cpus", "pids_limit")
            }
            self.compose(
                [
                    "exec",
                    "-T",
                    "research-postgres",
                    "createdb",
                    "-U",
                    "research_owner",
                    "research_capacity_probe",
                ]
            )
            thread.start()
            self.phase("workload", "research_capacity_probe")
            workload = [
                json.loads(line)
                for line in (self.out / "workload.jsonl").read_text().splitlines()
            ]
            manifest = next(
                item["manifest"] for item in workload if item["event"] == "manifest"
            )
            manifest_path = self.out / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True))
            with manifest_path.open("rb") as source:
                self.phase("verify-original", "research_capacity_probe", source)
            backup_path = (
                Path(tempfile.mkdtemp(prefix="groktocrawl-capacity-")) / "backup.dump"
            )
            with backup_path.open("wb") as target:
                self.timing(
                    "dump",
                    lambda: self.db(
                        "research_capacity_probe",
                        ["pg_dump", "--format=custom", "--no-owner", "--no-privileges"],
                        stdout=target,
                    ),
                )
            self.report["backup"] = {
                "format": "pg_dump custom",
                "bytes": backup_path.stat().st_size,
            }
            self.compose(
                [
                    "exec",
                    "-T",
                    "research-postgres",
                    "createdb",
                    "-U",
                    "research_owner",
                    "research_capacity_restore",
                ]
            )
            with backup_path.open("rb") as source:
                self.timing(
                    "restore",
                    lambda: self.db(
                        "research_capacity_restore",
                        [
                            "pg_restore",
                            "--single-transaction",
                            "--no-owner",
                            "--no-privileges",
                        ],
                        stdin=source,
                    ),
                )
            with manifest_path.open("rb") as source:
                self.phase("verify-restored", "research_capacity_restore", source)
            if "adapter_identity" not in self.report or self.report.get(
                "adapter_identity_changed"
            ):
                raise RuntimeError(
                    "adapter image/resource identity unavailable or changed"
                )
            self.report["status"] = "verified_feasibility_sample"
        except BaseException as exc:
            self.report["status"] = "failed"
            self.report["error_type"] = type(exc).__name__
            raise
        finally:
            self.stop.set()
            if thread.ident:
                thread.join(timeout=12)
            # A killed Compose client may leave its workload running. Stop it,
            # preserving the database and volume; do not weaken the probe deadline.
            if self.report["status"] != "verified_feasibility_sample":
                try:
                    subprocess.run(
                        ["docker", "stop", "--time", "10", self.container],
                        capture_output=True,
                        timeout=20,
                        check=False,
                    )
                except Exception as cleanup_error:
                    self.report["cleanup_error_type"] = type(cleanup_error).__name__
            for path in sorted(self.out.glob("*.jsonl")):
                if path.name == "resources.jsonl":
                    continue
                for line in path.read_text(errors="replace").splitlines():
                    try:
                        self.events.append(json.loads(line))
                    except json.JSONDecodeError:
                        self.report["truncated_event_stream"] = True
                        self.report["status"] = "failed"
            self.report["events"] = self.events
            self.report["operation_summaries"] = summaries(self.events)
            self.report["ingestion"] = [
                {
                    "phase": item["phase"],
                    "body_bytes": item["body_bytes"],
                    "elapsed_seconds": item["end"] - item["start"],
                    "body_bytes_per_second": item["body_bytes"]
                    / (item["end"] - item["start"]),
                }
                for item in self.events
                if item.get("event") == "phase"
            ]
            self.report["utc_end"] = datetime.now(UTC).isoformat()
            self.report["untested"] = [
                "full 1 GiB physical scope",
                "multiple processes",
                "large complete IR",
                "long histories",
                "vectors",
                "retention churn",
                "production recovery/SLOs",
                "independent semantic quality",
            ]
            (self.out / "result.json").write_text(json.dumps(self.report, indent=2))
            if self.report.get("truncated_event_stream"):
                raise RuntimeError("capacity evidence stream was truncated")


if __name__ == "__main__":
    Runner().run()
