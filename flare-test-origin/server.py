"""Local-only origin fixture for protected FlareSolverr integration tests."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

FIXTURE_HOST = os.environ.get("CAPTURE_FIXTURE_HOST", "flare-origin.test")
PRIVATE_SENTINEL_URL = os.environ.get(
    "CAPTURE_PRIVATE_SENTINEL_URL", "http://172.31.253.250:19001/probe"
).rstrip("/")
if not FIXTURE_HOST.endswith(".test"):
    raise RuntimeError("capture fixture host must use the reserved .test namespace")

SCRAPE_FIXTURE_HTML = b"""<!doctype html>
<html><head><title>Protected Ingress Fixture</title>
<meta name="description" content="Metadata returned through the scraper ingress pipeline.">
<meta property="og:description" content="A bounded local capture fixture.">
</head><body><main><h1>Protected ingress capture fixture</h1>
<p><code>INGRESS_SCRAPE_PIPELINE_OK</code> proves the HTTP bridge reached the real scraper.</p>
<p>This local page provides enough stable text for lightweight HTML extraction.</p>
<p>The fixture contains no external resources and performs no script or redirect.</p>
<p>Its metadata also verifies the separate metadata endpoint through the same socket.</p>
</main></body></html>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://{FIXTURE_HOST}/get")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/scrape-fixture":
            self._send(200, "text/html; charset=utf-8", SCRAPE_FIXTURE_HTML)
            return
        if self.path not in {"/", "/get"}:
            self.send_error(404)
            return
        cookie = self.headers.get("Cookie", "")
        body = f"""<!doctype html>
<html><head><title>Protected Flare Fixture</title></head><body>
<p>FLARE_GET_OK</p><p>COOKIE:{cookie}</p><p id="api-check">pending</p>
<p id="private-check">pending</p>
<script>
fetch('http://127.0.0.1:8191/health', {{mode:'no-cors'}})
  .then(() => document.getElementById('api-check').textContent='CONTROL_API_REACHABLE')
  .catch(() => document.getElementById('api-check').textContent='CONTROL_API_BLOCKED');
const privateProbe = new AbortController();
const nonceBytes = new Uint8Array(16);
crypto.getRandomValues(nonceBytes);
const privateNonce = Array.from(nonceBytes, value => value.toString(16).padStart(2, '0')).join('');
setTimeout(() => privateProbe.abort(), 1500);
fetch({json.dumps(f'{PRIVATE_SENTINEL_URL}/')} + privateNonce, {{mode:'cors', signal:privateProbe.signal}})
  .then(async response => {{
    const body = await response.text();
    const verified = response.status === 200
      && response.headers.get('content-type') === 'text/plain'
      && body === `PRIVATE_SENTINEL_RESPONSE_V1:${{privateNonce}}`;
    document.getElementById('private-check').textContent = verified
      ? 'PRIVATE_TARGET_REACHABLE' : 'PRIVATE_TARGET_UNEXPECTED_RESPONSE';
  }})
  .catch(() => document.getElementById('private-check').textContent='PRIVATE_TARGET_BLOCKED');
</script>
</body></html>""".encode()
        self._send(200, "text/html; charset=utf-8", body)

    def do_HEAD(self) -> None:
        """Return the same status and metadata as GET without a response body."""
        self.do_GET()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = parse_qs(body, keep_blank_values=True)
        marker = "&".join(f"{key}={value[0]}" for key, value in sorted(fields.items()))
        response = f"<!doctype html><html><head><title>Flare POST</title></head><body>FLARE_POST_OK:{marker}</body></html>".encode()
        self._send(200, "text/html; charset=utf-8", response)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), FixtureHandler).serve_forever()
