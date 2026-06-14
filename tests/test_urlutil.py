"""Unit tests for the shared URL utility module (common/url.py)."""

from common.url import (
    _METADATA_IPS,
    _PRIVATE_HOSTNAME_SUFFIXES,
    _PRIVATE_NETWORKS,
    extract_domain,
    is_private_host,
    is_same_origin,
    normalize_url,
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
