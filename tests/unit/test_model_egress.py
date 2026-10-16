from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CAPTURE_EGRESS = ROOT / "capture-egress-svc"
if str(CAPTURE_EGRESS) not in sys.path:
    sys.path.insert(0, str(CAPTURE_EGRESS))

from capture_egress_svc.model_proxy import _configured_authorities, make_model_proxy
from capture_egress_svc.proxy import configure_model_proxy_logging

from common.capture_destination import DestinationDeniedError


@pytest.mark.asyncio
async def test_model_proxy_only_connects_exact_configured_authorities():
    calls = []

    async def connector(authority, *, private_host_grants):
        calls.append((authority, private_host_grants))
        return object()

    proxy = make_model_proxy(
        frozenset({"llm-svc:4001", "api.example:443"}),
        frozenset({"llm-svc"}),
        connector=connector,
    )
    assert proxy.config.max_body_bytes == 8 * 1024 * 1024
    assert proxy.config.max_transfer_bytes == 32 * 1024 * 1024
    assert proxy.config.allowed_ports == frozenset({443, 4001})
    await proxy._connector("llm-svc:4001")
    await proxy._connector("api.example:443")
    assert calls == [
        ("llm-svc:4001", frozenset({"llm-svc"})),
        ("api.example:443", frozenset({"llm-svc"})),
    ]

    with pytest.raises(DestinationDeniedError):
        await proxy._connector("169.254.169.254:80")
    assert len(calls) == 2
    with pytest.raises(DestinationDeniedError):
        await proxy._connector("llm-svc:4002")
    assert len(calls) == 2


def test_model_proxy_authority_inventory_requires_explicit_port():
    with pytest.raises(DestinationDeniedError):
        make_model_proxy(frozenset({"llm-svc"}), frozenset())


def test_model_proxy_inventory_derives_authorities_only_from_fixed_bases(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example:9443/v1")
    monkeypatch.setenv("CAPTCHA_VISION_BASE_URL", "http://vision.example:8001/api")
    assert _configured_authorities() == frozenset(
        {"model.example:9443", "vision.example:8001"}
    )


def test_model_proxy_diagnostics_emit_at_info_without_root_logger_override(
    capsys,
):
    import logging

    logger = logging.getLogger("capture_egress_svc.proxy")
    root = logging.getLogger()
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    root_level = root.level
    try:
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)
        logger.propagate = True
        configure_model_proxy_logging()
        logger.info("model proxy event=response_eof bytes=12")
        assert "model proxy event=response_eof bytes=12" in capsys.readouterr().err
        assert root.level == root_level
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
