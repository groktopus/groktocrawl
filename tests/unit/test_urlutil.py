"""Unit tests for the shared URL utility module (common/url.py)."""

import pytest

from common.url import (
    _METADATA_IPS,
    _PRIVATE_HOSTNAME_SUFFIXES,
    _PRIVATE_NETWORKS,
    extract_domain,
    is_private_host,
    is_same_origin,
    normalize_url,
    validate_outbound_webhook_url,
)


class TestNormalizeUrl:
    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTP://EXAMPLE.COM/Path") == "http://example.com/path"

    def test_strips_trailing_slash(self):
        assert normalize_url("http://example.com/path/") == "http://example.com/path"

    def test_preserves_root_slash(self):
        assert normalize_url("http://example.com/") == "http://example.com/"

    def test_sorts_query_params(self):
        result = normalize_url("http://example.com/?b=2&a=1&c=3")
        assert result == "http://example.com/?a=1&b=2&c=3"

    def test_preserves_fragment(self):
        assert (
            normalize_url("http://example.com/#section")
            == "http://example.com/#section"
        )

    def test_preserves_port(self):
        assert (
            normalize_url("http://example.com:8080/path")
            == "http://example.com:8080/path"
        )


class TestExtractDomain:
    def test_simple_hostname(self):
        assert extract_domain("http://example.com/path") == "example.com"

    def test_with_scheme(self):
        assert (
            extract_domain("https://example.com/path", include_scheme=True)
            == "https://example.com"
        )

    def test_empty_url(self):
        assert extract_domain("") == ""

    def test_relative_url(self):
        assert extract_domain("/path/to/page") == ""

    def test_ip_address(self):
        assert extract_domain("http://93.184.216.34/test") == "93.184.216.34"


class TestIsSameOrigin:
    def test_same_origin(self):
        assert is_same_origin("http://example.com/a", "http://example.com/b")

    def test_different_scheme(self):
        assert not is_same_origin("http://example.com/a", "https://example.com/b")

    def test_different_host(self):
        assert not is_same_origin("http://example.com/a", "http://other.com/b")

    def test_port_matters(self):
        assert not is_same_origin(
            "http://example.com:8080/a", "http://example.com:9090/b"
        )


class TestIsPrivateHost:
    def test_loopback(self):
        assert is_private_host("http://127.0.0.1/test")
        assert is_private_host("http://localhost/test")

    def test_rfc1918_10(self):
        assert is_private_host("http://10.0.0.1/test")

    def test_rfc1918_192_168(self):
        assert is_private_host("http://192.168.1.1/test")

    def test_rfc1918_172_16(self):
        assert is_private_host("http://172.16.0.1/test")

    def test_metadata_ip(self):
        assert is_private_host("http://169.254.169.254/latest/meta-data/")

    def test_public_host(self):
        assert not is_private_host("http://example.com/test")

    def test_public_ip(self):
        assert not is_private_host("http://93.184.216.34/test")  # example.com

    def test_link_local(self):
        assert is_private_host("http://169.254.1.1/test")

    def test_empty_url(self):
        assert is_private_host("")

    def test_relative_url(self):
        assert is_private_host("/relative/path")

    def test_ipv4_mapped_loopback(self):
        assert is_private_host("http://[::ffff:127.0.0.1]/test")

    def test_ipv4_mapped_rfc1918(self):
        assert is_private_host("http://[::ffff:10.0.0.1]/test")
        assert is_private_host("http://[::ffff:172.16.5.5]/test")
        assert is_private_host("http://[::ffff:192.168.1.1]/test")

    def test_ipv4_mapped_metadata(self):
        assert is_private_host("http://[::ffff:169.254.169.254]/")

    def test_ipv4_mapped_public(self):
        assert not is_private_host("http://[::ffff:93.184.216.34]/test")

    def test_ipv4_compatible_private(self):
        assert is_private_host("http://[::10.0.0.1]/test")

    def test_6to4_private(self):
        assert is_private_host("http://[2002:0a00:0001::]/test")

    def test_6to4_public(self):
        assert not is_private_host("http://[2002:5db8:d822::]/test")

    def test_teredo_private(self):
        # Client IPv4 is XOR-obscured: 10.0.0.1 -> f5ff:fffe
        assert is_private_host("http://[2001::f5ff:fffe]/test")

    def test_teredo_public(self):
        # Client IPv4 is XOR-obscured: 93.184.216.34 -> a247:27dd
        assert not is_private_host("http://[2001::a247:27dd]/test")

    def test_nat64_private(self):
        assert is_private_host("http://[64:ff9b::a00:1]/test")
        assert is_private_host("http://[64:ff9b::7f00:1]/test")
        assert is_private_host("http://[64:ff9b::a9fe:a9fe]/test")

    def test_nat64_range_always_rejected(self):
        """The whole 64:ff9b::/32 special-purpose range is rejected.

        Covers the /96 well-known prefix, the /48 local-use prefix, and
        /64-length prefixes regardless of where IPv4 is embedded, since
        no legitimate public host lives in this IANA-reserved block.
        """
        assert is_private_host("http://[64:ff9b::5db8:d822]/test")
        assert is_private_host("http://[64:ff9b:1::1]/test")
        assert is_private_host("http://[64:ff9b:1::a00:1]/test")
        assert is_private_host("http://[64:ff9b::1:a00:1]/test")


class TestConstants:
    """Verify module-level constants are well-formed."""

    def test_private_networks_are_valid(self):
        for net in _PRIVATE_NETWORKS:
            # Just validate they parse — prefix lengths vary (/8, /16, /128, /7, /10)
            assert net.prefixlen > 0

    def test_metadata_ips_defined(self):
        assert len(_METADATA_IPS) >= 3

    def test_docker_hostname_suffixes(self):
        assert ".docker.internal" in _PRIVATE_HOSTNAME_SUFFIXES


class TestValidateOutboundWebhookUrl:
    """SSRF guard for outbound webhook destinations (issue #469)."""

    def test_accepts_public_https(self):
        assert validate_outbound_webhook_url("https://example.com/hook") is None

    def test_accepts_public_http(self):
        assert validate_outbound_webhook_url("http://example.com/hook") is None

    def test_accepts_public_ip(self):
        assert validate_outbound_webhook_url("https://93.184.216.34/hook") is None

    def test_rejects_missing_url(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("   ")

    def test_rejects_non_http_schemes(self):
        for bad in (
            "ftp://example.com/hook",
            "file:///etc/passwd",
            "gopher://example.com/hook",
            "//example.com/hook",  # scheme-less
            "example.com/hook",  # not a URL
        ):
            with pytest.raises(ValueError):
                validate_outbound_webhook_url(bad)

    def test_rejects_malformed_without_hostname(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("https:///path")

    def test_rejects_loopback(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://127.0.0.1:8080/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://localhost/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("https://[::1]/hook")

    def test_rejects_rfc1918(self):
        for host in ("10.0.0.1", "172.16.0.1", "192.168.1.1"):
            with pytest.raises(ValueError):
                validate_outbound_webhook_url(f"http://{host}/hook")

    def test_rejects_link_local_and_metadata(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://169.254.1.1/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_ipv6_ula_and_link_local(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("https://[fd00::1]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("https://[fe80::1]/hook")

    def test_rejects_multicast(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://224.0.0.1/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://239.255.255.250/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("https://[ff02::1]/hook")

    def test_rejects_ipv4_mapped_private(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[::ffff:127.0.0.1]:8080/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[::ffff:10.0.0.1]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[::ffff:192.168.1.1]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[::ffff:169.254.169.254]/hook")

    def test_accepts_ipv4_mapped_public(self):
        assert (
            validate_outbound_webhook_url("https://[::ffff:93.184.216.34]/hook") is None
        )

    def test_rejects_encapsulated_ipv4_private(self):
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[::10.0.0.1]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[2002:0a00:0001::]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[2001::f5ff:fffe]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[2002:7f00:0001::]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[64:ff9b::a00:1]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[64:ff9b::a9fe:a9fe]/hook")

    def test_rejects_nat64_range_at_validator_level(self):
        """Every 64:ff9b::/32 address is rejected, regardless of form."""
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[64:ff9b::5db8:d822]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[64:ff9b:1::1]/hook")
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://[64:ff9b:1::a00:1]/hook")

    def test_accepts_encapsulated_ipv4_public(self):
        assert validate_outbound_webhook_url("https://[2002:5db8:d822::]/hook") is None
        assert validate_outbound_webhook_url("https://[2001::a247:27dd]/hook") is None

    def test_rejects_hostname_resolving_to_ipv4_mapped_private(self, monkeypatch):
        from ipaddress import ip_address

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient",
            lambda hostname: ([ip_address("::ffff:10.0.0.7")], False),
        )
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://public.example.com/hook")

    def test_rejects_docker_internal_hostname(self, monkeypatch):
        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient", lambda hostname: ([], False)
        )
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://host.docker.internal:8080/hook")

    def test_rejects_hostname_resolving_to_private_ip(self, monkeypatch):
        from ipaddress import ip_address

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient",
            lambda hostname: ([ip_address("10.0.0.7")], False),
        )
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://public.example.com/hook")

    def test_rejects_hostname_resolving_to_metadata(self, monkeypatch):
        from ipaddress import ip_address

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient",
            lambda hostname: ([ip_address("169.254.169.254")], False),
        )
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://metadata.example.com/hook")

    def test_fails_closed_on_dns_failure(self, monkeypatch):
        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient", lambda hostname: ([], False)
        )
        with pytest.raises(ValueError):
            validate_outbound_webhook_url("http://unresolvable.example.invalid/hook")

    def test_transient_dns_failure_raises_retryable_error(self, monkeypatch):
        from common.url import WebhookDestinationDNSRetryableError

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient", lambda hostname: ([], True)
        )
        with pytest.raises(WebhookDestinationDNSRetryableError):
            validate_outbound_webhook_url("http://public.example.com/hook")

    def test_transient_dns_failure_is_validation_error(self, monkeypatch):
        from common.url import (
            WebhookDestinationDNSRetryableError,
            WebhookDestinationValidationError,
        )

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient", lambda hostname: ([], True)
        )
        try:
            validate_outbound_webhook_url("http://public.example.com/hook")
        except WebhookDestinationDNSRetryableError as exc:
            assert isinstance(exc, WebhookDestinationValidationError)
        else:
            raise AssertionError("expected retryable DNS error")

    def test_accepts_hostname_resolving_to_public_ip(self, monkeypatch):
        from ipaddress import ip_address

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient",
            lambda hostname: ([ip_address("93.184.216.34")], False),
        )
        assert validate_outbound_webhook_url("http://public.example.com/hook") is None

    def test_malformed_ipv6_brackets_rejected_cleanly(self):
        """Malformed IPv6 brackets never leak a raw ValueError."""
        from common.url import WebhookDestinationValidationError

        with pytest.raises(WebhookDestinationValidationError):
            validate_outbound_webhook_url("http://[::1/hook")

    def test_overlong_hostname_label_rejected_cleanly(self):
        """A >63-char label never leaks a UnicodeError."""
        from common.url import WebhookDestinationValidationError

        with pytest.raises(WebhookDestinationValidationError):
            validate_outbound_webhook_url(f"http://{'a' * 64}.example.com/hook")

    def test_overlong_hostname_label_is_permanent_rejection(self):
        from common.url import WebhookDestinationValidationError

        with pytest.raises(WebhookDestinationValidationError) as exc_info:
            validate_outbound_webhook_url(f"http://{'a' * 64}.example.com/hook")
        assert type(exc_info.value) is WebhookDestinationValidationError

    def test_unexpected_errors_become_permanent_rejection(self, monkeypatch):
        """The public wrapper converts any unexpected error to a rejection."""
        from common.url import (
            WebhookDestinationValidationError,
            validate_outbound_webhook_url,
        )

        def _boom(url):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("common.url._validate_outbound_webhook_url_inner", _boom)
        with pytest.raises(WebhookDestinationValidationError):
            validate_outbound_webhook_url("https://example.com/hook")
