"""Direct policy contract used by the isolated scraper DNS preflight."""

from common.url import is_private_host_literal


def test_literal_policy_preserves_private_suffix_and_ip_decisions():
    """Resolve no hostnames here; this is the deterministic first guard stage."""
    assert is_private_host_literal("") is True
    assert is_private_host_literal("gateway.docker.internal") is True
    assert is_private_host_literal("127.0.0.1") is True
    assert is_private_host_literal("93.184.216.34") is False
    assert is_private_host_literal("example.com") is None
