"""Contracts for the opt-in HTTPS API edge used by protected capture."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

from tests.outcome_governance import governed_skip

ROOT = Path(__file__).resolve().parents[2]


def _compose_command() -> list[str] | None:
    if shutil.which("docker"):
        return ["docker", "compose"]
    configured = os.environ.get("GROK_CONFIG_COMPOSE")
    if configured and Path(configured).is_file():
        return [configured]
    return None


class _ComposeLoader(yaml.SafeLoader):
    pass


def _reset_tag(loader: _ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!reset", _reset_tag)


def _load_yaml(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=_ComposeLoader)


def test_https_overlay_is_opt_in_and_replaces_plaintext_host_publication() -> None:
    base = _load_yaml(ROOT / "compose.experimental-candidate.yml")
    overlay = _load_yaml(ROOT / "compose.capture-https.yml")

    assert base["services"]["candidate-agent"]["ports"]
    for name in ("candidate-agent", "candidate-mcp"):
        assert base["services"][name]["ports"]
        assert overlay["services"][name]["ports"] == []
    frontend = overlay["services"]["candidate-capture-https"]
    assert frontend["image"].endswith("@sha256:56b887da77428b7a6621e59e480cdbd330cc805c22d3cedb66ceea76ffdea2c6")
    assert frontend["networks"] == ["candidate_https_edge", "candidate_private"]
    assert base["networks"]["candidate_private"]["internal"] is True
    assert "candidate_egress" not in frontend["networks"]
    edge = overlay["networks"]["candidate_https_edge"]
    assert edge.get("internal", False) is False
    assert edge["driver"] == "bridge"
    edge_subnet = ipaddress.ip_network(edge["ipam"]["config"][0]["subnet"])
    assert edge_subnet == ipaddress.ip_network("172.31.251.0/24")
    base_subnets = {}
    for name, network in base["networks"].items():
        subnet = network["ipam"]["config"][0]["subnet"]
        # Compose resolves this profile setting at runtime; validate its safe
        # default here without treating the interpolation syntax as a CIDR.
        if name == "candidate_capture":
            assert subnet == "${CAPTURE_HOST_BRIDGE_SUBNET:-172.31.254.0/29}"
            subnet = subnet.removeprefix("${CAPTURE_HOST_BRIDGE_SUBNET:-").removesuffix("}")
        base_subnets[name] = ipaddress.ip_network(subnet)
    assert all(not edge_subnet.overlaps(subnet) for subnet in base_subnets.values())
    assert frontend["ports"] == [
        "${CAPTURE_HTTPS_BIND_IP:-127.0.0.1}:${CAPTURE_HTTPS_HOST_PORT:-18443}:8443"
    ]
    assert frontend["cap_drop"] == ["ALL"]
    assert frontend["security_opt"] == ["no-new-privileges:true"]
    assert frontend["read_only"] is True
    assert frontend["user"] == "${CAPTURE_HTTPS_UID:-1000}:${CAPTURE_HTTPS_GID:-1000}"
    assert "uid=${CAPTURE_HTTPS_UID:-1000}" in frontend["tmpfs"][0]
    assert "gid=${CAPTURE_HTTPS_GID:-1000}" in frontend["tmpfs"][0]
    assert "test \"$(id -u)\" -ne 0" in frontend["command"][0]
    assert set(frontend["secrets"]) == {
        "capture_tls_certificate",
        "capture_tls_private_key",
        "capture_bearer_acl",
    }

    identities = json.loads((ROOT / "docs/runbooks/ci-public-image-identities.json").read_text())
    haproxy = next(row for row in identities["images"] if row["image"] == "library/haproxy:3.0-alpine")
    assert haproxy["upstream_digest"] == haproxy["cache_digest"]
    assert frontend["image"].endswith("@" + haproxy["upstream_digest"])


def test_frontend_config_is_tls_only_authenticated_and_route_allowlisted() -> None:
    config = (ROOT / "deploy/capture-https.cfg").read_text()
    lines = [line.strip() for line in config.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    assert "bind :8443 ssl crt /tmp/capture-server.pem ssl-min-ver TLSv1.2 alpn http/1.1" in lines
    assert "acl duplicate_authorization req.hdr_cnt(Authorization) gt 1" in lines
    assert "http-request deny deny_status 400 if duplicate_authorization" in lines
    assert "acl allowed_bearer req.hdr(Authorization) -m str -f /run/secrets/capture_bearer_acl" in lines
    assert "http-request deny deny_status 401 unless allowed_bearer" in lines
    assert "acl health_method method GET" in lines
    assert "acl health_path path -m str /health" in lines
    assert "acl scrape_method method POST" in lines
    assert "acl scrape_path path -m str /v2/scrape" in lines
    assert "acl request_has_query query -m found" in lines
    assert "http-request deny deny_status 404 if request_has_query" in lines
    assert (
        "http-request deny deny_status 404 unless health_method health_path or scrape_method scrape_path"
        in lines
    )
    assert "retries 0" in lines
    assert not any(re.match(r"(?:no\s+)?log(?:\s|$)", line) for line in lines)
    assert "default_backend candidate_agent_api" in lines
    assert any(line.startswith("server-template agent 1-1 candidate-agent:8080") for line in lines)
    assert "http-request set-header Authorization" not in config


def test_overlay_has_no_bearer_or_certificate_values_in_compose_literals() -> None:
    overlay = _load_yaml(ROOT / "compose.capture-https.yml")
    for name, secret in overlay["secrets"].items():
        assert name in {"capture_tls_certificate", "capture_tls_private_key", "capture_bearer_acl"}
        assert set(secret) == {"file"}
        assert isinstance(secret["file"], str)
        assert secret["file"].startswith("${")
    assert not re.search(
        r"Bearer\s+[A-Za-z0-9_-]{32,}",
        (ROOT / "compose.capture-https.yml").read_text(),
    )
    assert "CAPTURE_BEARER_ACL_FILE" in (ROOT / "compose.capture-https.yml").read_text()


def test_compose_resolves_overlay_and_resets_plaintext_api_ports() -> None:
    compose = _compose_command()
    if compose is None:
        governed_skip(
            "Docker Compose rendering requires a local Docker installation",
            owner="capture-ingress",
            issue="#429",
            classification="retained",
            environment="Docker Compose unavailable",
        )
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        (temp_path / "cert.pem").write_text("local test certificate placeholder\n")
        (temp_path / "key.pem").write_text("local test private key placeholder\n")
        (temp_path / "bearer.txt").write_text("Bearer local-only-test-token\n")
        (temp_path / "postgres.txt").write_text("local-only-test-password\n")
        env_file = temp_path / "candidate.env"
        env_file.write_text(
            "\n".join(
                (
                    "CANDIDATE_API_KEY=local-only-test-token",
                    "CANDIDATE_POSTGRES_PASSWORD_FILE=" + str(temp_path / "postgres.txt"),
                    "LLM_API_KEY=local-only-test-key",
                    "LLM_BASE_URL=http://127.0.0.1:1/v1",
                    "CAPTURE_TLS_CERT_FILE=" + str(temp_path / "cert.pem"),
                    "CAPTURE_TLS_KEY_FILE=" + str(temp_path / "key.pem"),
                    "CAPTURE_BEARER_ACL_FILE=" + str(temp_path / "bearer.txt"),
                )
            )
            + "\n"
        )
        result = subprocess.run(
            [
                *compose,
                "--project-name",
                "capture-contract",
                "--env-file",
                str(env_file),
                "-f",
                str(ROOT / "compose.experimental-candidate.yml"),
                "-f",
                str(ROOT / "compose.capture-https.yml"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        rendered = json.loads(result.stdout)
        agent = rendered["services"]["candidate-agent"]
        assert not agent.get("ports")
        assert not rendered["services"]["candidate-mcp"].get("ports")
        assert {name for name, service in rendered["services"].items() if service.get("ports")} == {
            "candidate-capture-https"
        }
        frontend = rendered["services"]["candidate-capture-https"]
        assert set(frontend["networks"]) == {"candidate_https_edge", "candidate_private"}
        assert rendered["networks"]["candidate_private"]["internal"] is True
        assert "candidate_egress" not in frontend["networks"]
        edge = rendered["networks"]["candidate_https_edge"]
        assert edge.get("internal", False) is False
        assert edge["driver"] == "bridge"
        assert ipaddress.ip_network(edge["ipam"]["config"][0]["subnet"]) == (
            ipaddress.ip_network("172.31.251.0/24")
        )
        assert {
            name
            for name, service in rendered["services"].items()
            if "candidate_https_edge" in service.get("networks", {})
        } == {"candidate-capture-https"}
        assert frontend["ports"][0]["published"] == "18443"
        assert frontend["ports"][0]["host_ip"] == "127.0.0.1"
        assert frontend["user"] == "1000:1000"
        assert "uid=1000" in frontend["tmpfs"][0]
        assert "gid=1000" in frontend["tmpfs"][0]
        assert "environment" not in frontend
        assert "local-only-test-token" not in json.dumps(frontend)


def test_local_haproxy_enforces_verified_tls_bearer_and_exact_routes() -> None:
    if shutil.which("haproxy") is None:
        governed_skip(
            "Local HAProxy TLS integration requires the HAProxy binary",
            owner="capture-ingress",
            issue="#429",
            classification="retained",
            environment="HAProxy binary unavailable",
        )
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    token = "local-only-test-token"

    class StubAgent(BaseHTTPRequestHandler):
        def do_GET(self):
            self._respond()

        def do_POST(self):
            self._respond()

        def _respond(self):
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(403)
                return
            if self.command == "GET" and self.path == "/health":
                status, payload = 200, {"status": "ok"}
            elif self.command == "POST" and self.path == "/v2/scrape":
                status, payload = 200, {"success": True}
            else:
                status, payload = 404, {"detail": "not found"}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    def free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Capture test CA")])
    now = datetime.now(UTC)
    def issue_test_ca(name, key):
        return (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=None,
                    decipher_only=None,
                ),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )

    ca_cert = issue_test_ca(ca_name, ca_key)
    wrong_ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Wrong test CA")])
    wrong_ca_cert = issue_test_ca(wrong_ca_name, wrong_ca_key)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        ca_path = temp_path / "ca.pem"
        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        trusted_context = ssl.create_default_context(cafile=str(ca_path))
        wrong_ca_path = temp_path / "wrong-ca.pem"
        wrong_ca_path.write_bytes(wrong_ca_cert.public_bytes(serialization.Encoding.PEM))
        wrong_ca_context = ssl.create_default_context(cafile=str(wrong_ca_path))
        server_path = temp_path / "server.pem"
        server_path.write_bytes(
            server_cert.public_bytes(serialization.Encoding.PEM)
            + server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        bearer_path = temp_path / "bearer.txt"
        bearer_path.write_text(f"Bearer {token}\n")

        backend_port = free_port()
        frontend_port = free_port()
        while frontend_port == backend_port:
            frontend_port = free_port()
        backend = ThreadingHTTPServer(("127.0.0.1", backend_port), StubAgent)
        server_thread = threading.Thread(target=backend.serve_forever, daemon=True)
        server_thread.start()

        source_config = (ROOT / "deploy/capture-https.cfg").read_text()
        source_config = source_config.replace(":8443", f"127.0.0.1:{frontend_port}")
        source_config = source_config.replace("/tmp/capture-server.pem", str(server_path))
        source_config = source_config.replace(
            "/run/secrets/capture_bearer_acl", str(bearer_path)
        )
        source_config = re.sub(
            r"server-template agent 1-1 candidate-agent:8080 .*",
            f"server agent 127.0.0.1:{backend_port} check",
            source_config,
        )
        config_path = temp_path / "haproxy.cfg"
        config_path.write_text(source_config)
        check = subprocess.run(
            ["haproxy", "-c", "-f", str(config_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert check.returncode == 0, check.stderr
        proxy = subprocess.Popen(
            ["haproxy", "-W", "-db", "-f", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if proxy.poll() is not None:
                    stdout, stderr = proxy.communicate()
                    raise AssertionError(f"HAProxy exited: {stdout!r} {stderr!r}")
                try:
                    with socket.create_connection(("127.0.0.1", frontend_port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise AssertionError("HAProxy listener did not start")

            url = f"https://127.0.0.1:{frontend_port}"
            with httpx.Client(verify=trusted_context, trust_env=False, timeout=2) as client:
                assert client.get(url + "/health").status_code == 401
                assert client.get(url + "/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
                assert client.get(
                    url + "/health", headers={"Authorization": f"Bearer {token}"}
                ).json() == {"status": "ok"}
                assert client.post(
                    url + "/v2/scrape",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"url": "https://example.invalid/"},
                ).json() == {"success": True}
                assert client.get(
                    url + "/v2/scrape", headers={"Authorization": f"Bearer {token}"}
                ).status_code == 404
                assert client.post(
                    url + "/health", headers={"Authorization": f"Bearer {token}"}
                ).status_code == 404
                assert client.get(
                    url + "/health?extra=1", headers={"Authorization": f"Bearer {token}"}
                ).status_code == 404
                assert client.get(
                    url + "/metrics", headers={"Authorization": f"Bearer {token}"}
                ).status_code == 404

            with pytest.raises(httpx.ConnectError):
                httpx.get(
                    url + "/health",
                    headers={"Authorization": f"Bearer {token}"},
                    verify=wrong_ca_context,
                    trust_env=False,
                    timeout=2,
                )
            with pytest.raises(httpx.ConnectError):
                httpx.get(
                    f"https://localhost:{frontend_port}/health",
                    headers={"Authorization": f"Bearer {token}"},
                    verify=trusted_context,
                    trust_env=False,
                    timeout=2,
                )
        finally:
            proxy.terminate()
            try:
                proxy.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=3)
            backend.shutdown()
            backend.server_close()
