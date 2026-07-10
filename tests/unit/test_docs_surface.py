"""Regression tests for the public documentation surface guardrail."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker():
    script = Path(__file__).resolve().parents[2] / "scripts" / "check-docs-surface.py"
    spec = importlib.util.spec_from_file_location("check_docs_surface", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_omission_is_reported():
    checker = _load_checker()
    errors = checker.compare("API routes", {"POST /v2/new"}, set())
    assert errors == ["API routes: undocumented: POST /v2/new"]


def test_cli_omission_is_reported():
    checker = _load_checker()
    errors = checker.compare("CLI commands", {"new-command"}, set())
    assert errors == ["CLI commands: undocumented: new-command"]


def test_service_omission_is_reported():
    checker = _load_checker()
    errors = checker.compare("Compose services", {"new-svc"}, set())
    assert errors == ["Compose services: undocumented: new-svc"]


def test_configuration_omission_is_reported():
    checker = _load_checker()
    errors = checker.compare("Configuration keys", {"NEW_SETTING"}, set())
    assert errors == ["Configuration keys: undocumented: NEW_SETTING"]
