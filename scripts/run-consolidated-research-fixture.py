#!/usr/bin/env python3
"""Write a fresh directory of synthetic research reports without network/provider use."""

import argparse
import asyncio
from pathlib import Path

from agent.experimental.consolidated_example import example_journey


async def main(output: Path) -> None:
    result = await example_journey().run()
    output.mkdir(parents=True, exist_ok=False)
    for report in result.reports:
        (output / f"{report.artifact.layer}.md").write_bytes(report.body)
    (output / "knowledge.json").write_bytes(result.knowledge_bytes)
    (output / "manifest.json").write_bytes(result.manifest_bytes)
    print(f"Fixture-only reports: {output.resolve()}")
    print("Coverage: partial. No committed publication or research-quality claim.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New directory for fixture reports")
    asyncio.run(main(parser.parse_args().output))
