"""Operator-only reconciliation after quiescing other endpoint clients."""

import argparse
import os
from pathlib import Path

from .checkpoints import Checkpoints
from .vision_ocr import OcrSettings, positive_env, reconcile_idle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-quiesced",
        action="store_true",
        required=True,
        help="Confirm other producers are stopped for this check",
    )
    parser.parse_args()
    settings = OcrSettings.load()
    settings.validate_model()
    store = Checkpoints(
        Path(os.environ["PARSE_STATE_DIR"]),
        positive_env("PARSE_STATE_MAX_BYTES", 1073741824),
        positive_env("PARSE_STATE_TTL_SECONDS", 604800),
    )
    changed = reconcile_idle(store, settings)
    print(  # noqa: T201 -- operator CLI output
        "Unknown lane reconciled; attempt history retained."
        if changed
        else "Lane already clear."
    )


if __name__ == "__main__":
    main()
