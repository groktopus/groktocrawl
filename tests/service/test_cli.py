"""Tests for the groktocrawl CLI — crawl subcommand flags and error handling.

Covers:
- Client.crawl() parameter mapping to API
- cmd_crawl() handler behavior with new flags
- JSON output formatting
- Server-unreachable error handling
- Help text completeness
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Load the CLI script ──────────────────────────────────────────────────────

_CLI_PATH = Path(__file__).resolve().parents[2] / "groktocrawl"
if not _CLI_PATH.is_file():
    pytest.skip("groktocrawl CLI not found at project root", allow_module_level=True)
_CLI_PATH = str(_CLI_PATH)

# Load the CLI module by executing it with a namespace dict
_cli_ns: dict = {}
with open(_CLI_PATH, encoding="utf-8") as f:
    _code = compile(f.read(), _CLI_PATH, "exec")
exec(_code, _cli_ns)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Create a Client instance with dry_run=False for testing."""
    client_cls = _cli_ns["Client"]
    return client_cls(server="http://test-server:8080", dry_run=False)


# ── Client.crawl() tests ─────────────────────────────────────────────────────


class TestClientAuthentication:
    """Tests for Client._request() authentication headers."""

    def test_namespaced_key_takes_precedence_and_sends_bearer_auth(
        self, client, monkeypatch
    ):
        """GROKTOCRAWL_API_KEY takes precedence over API_KEY."""
        monkeypatch.setenv("GROKTOCRAWL_API_KEY", "namespaced-test-key")
        monkeypatch.setenv("API_KEY", "fallback-test-key")

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"success": True}
        response.headers = {}
        response.url = "http://test-server:8080/v2/health"

        import requests as requests_module

        with patch.object(requests_module, "request", return_value=response) as request:
            client._request("GET", "/health")

        assert request.call_args.kwargs["headers"] == {
            "Authorization": "Bearer namespaced-test-key"
        }

    def test_no_key_sends_no_authorization_header(self, client, monkeypatch):
        """Client requests without configured credentials omit Authorization."""
        monkeypatch.delenv("GROKTOCRAWL_API_KEY", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"success": True}
        response.headers = {}
        response.url = "http://test-server:8080/v2/health"

        import requests as requests_module

        with patch.object(requests_module, "request", return_value=response) as request:
            client._request("GET", "/health")

        assert "Authorization" not in request.call_args.kwargs["headers"]

    def test_api_error_preserves_structured_captcha_body(self, client):
        response = MagicMock()
        response.status_code = 502
        response.json.return_value = {
            "success": False,
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "details": {"provider": "recaptcha"},
        }
        response.headers = {}
        response.url = "http://test-server:8080/v2/scrape"

        import requests as requests_module

        with patch.object(requests_module, "request", return_value=response):
            with pytest.raises(_cli_ns["ApiError"]) as error:
                client._request(
                    "POST", "/scrape", json_data={"url": "https://example.test"}
                )

        assert error.value.status_code == 502
        assert error.value.body["error_code"] == "CAPTCHA_UNRESOLVED"

    def test_json_die_emits_structured_api_error(self, capsys):
        original_json = _cli_ns["JSON_OUTPUT"]
        _cli_ns["JSON_OUTPUT"] = True
        error = _cli_ns["ApiError"](
            "API error (502)",
            status_code=502,
            body={"error_code": "CAPTCHA_UNRESOLVED", "success": False},
        )
        try:
            with pytest.raises(SystemExit) as exit_info:
                _cli_ns["die"](error)
        finally:
            _cli_ns["JSON_OUTPUT"] = original_json

        assert exit_info.value.code == 1
        assert json.loads(capsys.readouterr().out)["error_code"] == "CAPTCHA_UNRESOLVED"


class TestClientCrawl:
    """Tests for Client.crawl() parameter mapping."""

    def test_basic_crawl_sends_correct_data(self, client):
        """Basic crawl sends url, limit, and max_depth."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["url"] == "http://example.com"
            assert json_data["limit"] == 0
            assert json_data["max_depth"] == 2
            return {"success": True, "id": "crawl-1234-uuid"}

        client._request = _fake_request
        result = client.crawl(url="http://example.com")
        assert result["id"] == "crawl-1234-uuid"

    def test_crawl_sends_include_paths(self, client):
        """include_paths is passed as include_paths."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["include_paths"] == ["/blog/*", "/docs/*"]
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            include_paths=["/blog/*", "/docs/*"],
        )
        assert result["id"] == "job-1"

    def test_crawl_sends_exclude_paths(self, client):
        """exclude_paths is passed as exclude_paths."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["exclude_paths"] == ["/admin/*"]
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            exclude_paths=["/admin/*"],
        )
        assert result["id"] == "job-1"

    def test_crawl_sends_max_pages(self, client):
        """max_pages is passed as max_pages."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["max_pages"] == 5
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            max_pages=5,
        )
        assert result["id"] == "job-1"

    def test_crawl_sends_max_depth(self, client):
        """max_depth is passed as max_depth."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["max_depth"] == 1
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            max_depth=1,
        )
        assert result["id"] == "job-1"

    def test_crawl_sends_ignore_query_parameters(self, client):
        """ignore_query_parameters=True sends ignore_query_parameters."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["ignore_query_parameters"] is True
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            ignore_query_parameters=True,
        )
        assert result["id"] == "job-1"

    def test_crawl_does_not_send_ignore_query_parameters_by_default(self, client):
        """ignore_query_parameters=False does not send the field."""

        def _fake_request(method, path, json_data=None, params=None):
            assert "ignore_query_parameters" not in json_data
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            ignore_query_parameters=False,
        )
        assert result["id"] == "job-1"

    def test_crawl_does_not_send_max_pages_when_none(self, client):
        """max_pages=None does not send max_pages."""

        def _fake_request(method, path, json_data=None, params=None):
            assert "max_pages" not in json_data
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            max_pages=None,
        )
        assert result["id"] == "job-1"

    def test_crawl_sends_both_limit_and_max_pages(self, client):
        """Both limit and max_pages can be sent together."""

        def _fake_request(method, path, json_data=None, params=None):
            assert json_data["limit"] == 50
            assert json_data["max_pages"] == 5
            return {"success": True, "id": "job-1"}

        client._request = _fake_request
        result = client.crawl(
            url="http://example.com",
            limit=50,
            max_pages=5,
        )
        assert result["id"] == "job-1"


# ── Connection error handling ────────────────────────────────────────────────


class TestConnectionErrorHandling:
    """Tests that connection errors produce clean messages without tracebacks."""

    def test_connection_error_is_caught_and_clean(self, client):
        """ConnectionError in _request raises ApiError with clean message."""
        api_error_cls = _cli_ns["ApiError"]
        with patch.object(
            client,
            "_request",
            side_effect=api_error_cls(
                "Cannot connect to http://test-server:8080: "
                "ConnectionRefusedError(61, 'Connection refused')\n"
                "  Is the server running? Set --server or GROKTOCRAWL_API_URL"
            ),
        ):
            with pytest.raises(api_error_cls) as exc_info:
                client.crawl(url="http://example.com")
            msg = str(exc_info.value)
            assert "Cannot connect" in msg
            assert "Is the server running" in msg
            assert "Traceback" not in msg

    def test_cmd_crawl_connection_error_exits_nonzero(self):
        """cmd_crawl exits non-zero with clean error message on connection error."""
        api_error_cls = _cli_ns["ApiError"]
        cmd_crawl = _cli_ns["cmd_crawl"]

        mock_args = MagicMock()
        mock_args.url = "http://example.com"
        mock_args.limit = 50
        mock_args.max_depth = 2
        mock_args.include_paths = None
        mock_args.exclude_paths = None
        mock_args.max_pages = None
        mock_args.ignore_query_parameters = False
        mock_args.no_poll = True
        mock_args.dry_run = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.crawl.side_effect = api_error_cls(
            "Cannot connect to http://localhost:8080: Connection refused"
        )

        stderr = _capture_stderr(cmd_crawl, mock_client, mock_args)
        assert "Error:" in stderr
        assert "Cannot connect" in stderr
        assert "Traceback" not in stderr


# ── cmd_crawl handler tests ──────────────────────────────────────────────────


class TestCmdCrawl:
    """Tests for the cmd_crawl() handler function."""

    def test_cmd_crawl_sends_new_flags(self):
        """cmd_crawl passes max_pages and ignore_query_parameters to client.crawl()."""
        cmd_crawl = _cli_ns["cmd_crawl"]

        mock_args = MagicMock()
        mock_args.url = "http://example.com"
        mock_args.limit = 50
        mock_args.max_depth = 2
        mock_args.include_paths = None
        mock_args.exclude_paths = None
        mock_args.max_pages = 5
        mock_args.ignore_query_parameters = True
        mock_args.no_poll = True
        mock_args.dry_run = False
        mock_args.format = None

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.crawl.return_value = {"success": True, "id": "job-1"}

        # With no_poll=True, cmd_crawl returns normally after printing job ID
        cmd_crawl(mock_client, mock_args)

        mock_client.crawl.assert_called_once_with(
            url="http://example.com",
            limit=50,
            max_depth=2,
            include_paths=None,
            exclude_paths=None,
            max_pages=5,
            ignore_query_parameters=True,
        )

    def test_cmd_crawl_json_output(self):
        """cmd_crawl with JSON_OUTPUT produces valid JSON."""
        cmd_crawl = _cli_ns["cmd_crawl"]

        mock_args = MagicMock()
        mock_args.url = "http://example.com"
        mock_args.limit = 1
        mock_args.max_depth = 2
        mock_args.include_paths = None
        mock_args.exclude_paths = None
        mock_args.max_pages = None
        mock_args.ignore_query_parameters = False
        mock_args.dry_run = False
        mock_args.no_poll = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.crawl.return_value = {"success": True, "id": "job-json-test"}
        mock_client.crawl_status.return_value = {
            "success": True,
            "status": "completed",
            "completed": 2,
            "total": 2,
            "data": [
                {"url": "http://example.com/page1", "markdown": "# Page 1"},
                {"url": "http://example.com/page2", "markdown": "# Page 2"},
            ],
        }

        original_json = _cli_ns["JSON_OUTPUT"]
        _cli_ns["JSON_OUTPUT"] = True
        try:
            stdout = _capture_stdout(cmd_crawl, mock_client, mock_args)
        finally:
            _cli_ns["JSON_OUTPUT"] = original_json

        output = stdout.strip()
        # Output should be valid JSON
        parsed = json.loads(output)
        assert parsed["job_id"] == "job-json-test"
        assert parsed["status"] == "completed"
        assert len(parsed["pages"]) == 2

    def test_cmd_crawl_polling_error_retry_limit(self):
        """cmd_crawl exits after max polling errors."""
        cmd_crawl = _cli_ns["cmd_crawl"]
        api_error_cls = _cli_ns["ApiError"]

        mock_args = MagicMock()
        mock_args.url = "http://example.com"
        mock_args.limit = 50
        mock_args.max_depth = 2
        mock_args.include_paths = None
        mock_args.exclude_paths = None
        mock_args.max_pages = None
        mock_args.ignore_query_parameters = False
        mock_args.dry_run = False
        mock_args.no_poll = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.crawl.return_value = {"success": True, "id": "job-poll-err"}
        # All status checks fail
        mock_client.crawl_status.side_effect = api_error_cls(
            "Status check failed: connection lost"
        )

        stderr = _capture_stderr(cmd_crawl, mock_client, mock_args)
        assert "Lost connection" in stderr
        assert "Traceback" not in stderr


def _capture_stdout(func, *args, **kwargs):
    """Run func and capture stdout, returning the string."""
    from contextlib import redirect_stdout, suppress
    from io import StringIO

    buf = StringIO()
    with redirect_stdout(buf), suppress(SystemExit):
        func(*args, **kwargs)
    return buf.getvalue()


def _capture_stderr(func, *args, **kwargs):
    """Run func and capture stderr, returning the string."""
    from contextlib import redirect_stderr, suppress
    from io import StringIO

    buf = StringIO()
    with redirect_stderr(buf), suppress(SystemExit):
        func(*args, **kwargs)
    return buf.getvalue()


# ── Parser / help text tests ─────────────────────────────────────────────────


class TestCrawlParser:
    """Tests for the crawl subcommand argument parser."""

    def test_help_contains_crawl_flags(self):
        """crawl --help lists all flags including new ones."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()

        from contextlib import redirect_stdout
        from io import StringIO

        stdout = StringIO()
        with pytest.raises(SystemExit):
            with redirect_stdout(stdout):
                parser.parse_args(["crawl", "--help"])
        help_text = stdout.getvalue()

        # Existing flags
        assert "--limit" in help_text
        assert "--max-depth" in help_text
        assert "--include-paths" in help_text
        assert "--exclude-paths" in help_text
        assert "--no-poll" in help_text

        # New flags
        assert "--max-pages" in help_text
        assert "--ignore-query-params" in help_text

    def test_parse_crawl_args_max_pages(self):
        """--max-pages is parsed correctly."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["crawl", "http://example.com", "--max-pages", "5"])
        assert args.max_pages == 5

    def test_parse_crawl_args_max_pages_default_none(self):
        """--max-pages defaults to None."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["crawl", "http://example.com"])
        assert args.max_pages is None

    def test_parse_crawl_args_ignore_query_params(self):
        """--ignore-query-params is parsed correctly."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(
            ["crawl", "http://example.com", "--ignore-query-params"]
        )
        assert args.ignore_query_parameters is True

    def test_parse_crawl_args_ignore_query_params_default(self):
        """--ignore-query-params defaults to False."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["crawl", "http://example.com"])
        assert args.ignore_query_parameters is False

    def test_parse_crawl_args_include_paths(self):
        """--include-paths parses multiple path patterns."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(
            [
                "crawl",
                "http://example.com",
                "--include-paths",
                "/blog/*",
                "/docs/*",
            ]
        )
        assert args.include_paths == ["/blog/*", "/docs/*"]

    def test_parse_crawl_args_exclude_paths(self):
        """--exclude-paths parses multiple path patterns."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(
            [
                "crawl",
                "http://example.com",
                "--exclude-paths",
                "/admin/*",
            ]
        )
        assert args.exclude_paths == ["/admin/*"]

    def test_parse_crawl_args_max_depth(self):
        """--max-depth is parsed correctly."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["crawl", "http://example.com", "--max-depth", "1"])
        assert args.max_depth == 1

    def test_parse_crawl_args_limit(self):
        """--limit is parsed correctly."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["crawl", "http://example.com", "--limit", "10"])
        assert args.limit == 10

    def test_global_help_shows_crawl(self):
        """Top-level --help lists the crawl subcommand."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()

        from contextlib import redirect_stdout
        from io import StringIO

        stdout = StringIO()
        with pytest.raises(SystemExit):
            with redirect_stdout(stdout):
                parser.parse_args(["--help"])
        help_text = stdout.getvalue()
        assert "crawl" in help_text


# ── Subprocess integration tests ──────────────────────────────────────────────


class TestCLISubprocess:
    """Integration tests that run the CLI as a subprocess."""

    def test_help_shows_crawl_flags(self):
        """Running groktocrawl crawl --help shows all flags."""
        result = subprocess.run(
            [sys.executable, _CLI_PATH, "crawl", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        # Old flags
        assert "--limit" in result.stdout
        assert "--max-depth" in result.stdout
        assert "--include-paths" in result.stdout
        assert "--exclude-paths" in result.stdout
        assert "--no-poll" in result.stdout
        # New flags
        assert "--max-pages" in result.stdout
        assert "--ignore-query-params" in result.stdout

    def test_top_level_help_shows_crawl(self):
        """Top-level --help lists crawl command."""
        result = subprocess.run(
            [sys.executable, _CLI_PATH, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "crawl" in result.stdout


# ── cmd_batch_scrape handler tests ────────────────────────────────────────────


class TestCmdBatchScrape:
    """Tests for the cmd_batch_scrape() handler function."""

    def test_default_shows_status(self):
        """cmd_batch_scrape calls batch_status() when no flag is given."""
        cmd_batch_scrape = _cli_ns["cmd_batch_scrape"]
        mock_args = MagicMock()
        mock_args.job_id = "batch-job-1"
        mock_args.cancel = False
        mock_args.errors = False
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.batch_status.return_value = {
            "success": True,
            "status": "completed",
            "completed": 3,
            "total": 3,
            "data": [
                {"url": "http://example.com/1", "markdown": "# Page 1"},
                {"url": "http://example.com/2", "markdown": "# Page 2"},
                {"url": "http://example.com/3", "markdown": "# Page 3"},
            ],
        }
        cmd_batch_scrape(mock_client, mock_args)
        mock_client.batch_status.assert_called_once_with("batch-job-1")

    def test_cancel_flag(self):
        """--cancel calls cancel_batch()."""
        cmd_batch_scrape = _cli_ns["cmd_batch_scrape"]
        mock_args = MagicMock()
        mock_args.job_id = "batch-job-2"
        mock_args.cancel = True
        mock_args.errors = False
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.cancel_batch.return_value = {"success": True}
        cmd_batch_scrape(mock_client, mock_args)
        mock_client.cancel_batch.assert_called_once_with("batch-job-2")
        mock_client.batch_status.assert_not_called()

    def test_errors_flag(self):
        """--errors calls batch_errors()."""
        cmd_batch_scrape = _cli_ns["cmd_batch_scrape"]
        mock_args = MagicMock()
        mock_args.job_id = "batch-job-3"
        mock_args.cancel = False
        mock_args.errors = True
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.batch_errors.return_value = {
            "success": True,
            "errors": [
                {"url": "http://example.com/bad", "error": "timeout"},
            ],
        }
        cmd_batch_scrape(mock_client, mock_args)
        mock_client.batch_errors.assert_called_once_with("batch-job-3")
        mock_client.batch_status.assert_not_called()

    def test_json_output(self):
        """JSON_OUTPUT=True produces valid JSON."""
        cmd_batch_scrape = _cli_ns["cmd_batch_scrape"]
        mock_args = MagicMock()
        mock_args.job_id = "batch-json-1"
        mock_args.cancel = False
        mock_args.errors = False
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.batch_status.return_value = {
            "success": True,
            "status": "completed",
            "completed": 2,
            "total": 2,
            "data": [
                {"url": "http://example.com/a", "markdown": "# A"},
                {"url": "http://example.com/b", "markdown": "# B"},
            ],
        }
        original_json = _cli_ns["JSON_OUTPUT"]
        _cli_ns["JSON_OUTPUT"] = True
        try:
            stdout = _capture_stdout(cmd_batch_scrape, mock_client, mock_args)
        finally:
            _cli_ns["JSON_OUTPUT"] = original_json
        output = stdout.strip()
        parsed = json.loads(output)
        assert parsed["status"] == "completed"
        assert parsed["completed"] == 2
        assert parsed["total"] == 2

    def test_api_error_handled(self):
        """ApiError in cmd_batch_scrape exits with clean error message."""
        cmd_batch_scrape = _cli_ns["cmd_batch_scrape"]
        api_error_cls = _cli_ns["ApiError"]
        mock_args = MagicMock()
        mock_args.job_id = "batch-err-1"
        mock_args.cancel = False
        mock_args.errors = False
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.batch_status.side_effect = api_error_cls(
            "Cannot connect to http://test-server:8080: Connection refused"
        )
        stderr = _capture_stderr(cmd_batch_scrape, mock_client, mock_args)
        assert "Error:" in stderr
        assert "Cannot connect" in stderr
        assert "Traceback" not in stderr


# ── Client.parse_upload_file / parse_with_upload_id tests ──────────────────────


class TestClientParseUpload:
    """Tests for Client.parse_upload_file() and Client.parse_with_upload_id()."""

    def test_parse_upload_file_sends_correct_data(self, client):
        """parse_upload_file sends PUT with correct URL, headers, and body."""

        def _fake_put(url, data=None, headers=None, timeout=None):
            assert "/parse/upload/my-upload-1" in url
            assert headers["Content-Type"] == "application/pdf"
            # X-Filename should match the temp file's basename
            assert headers["X-Filename"].endswith(".pdf")
            assert data == b"fake pdf content"

            class FakeResp:
                status_code = 200

                def json(self):
                    return {"success": True, "upload_id": "my-upload-1"}

            return FakeResp()

        import requests as _requests_module

        with patch.object(_requests_module, "put", side_effect=_fake_put):
            import tempfile

            with tempfile.NamedTemporaryFile(
                suffix=".pdf", delete=False, mode="wb"
            ) as tmp:
                tmp.write(b"fake pdf content")
                tmp_path = tmp.name
            try:
                result = client.parse_upload_file("my-upload-1", tmp_path)
                assert result["success"] is True
                assert result["upload_id"] == "my-upload-1"
            finally:
                os.unlink(tmp_path)

    def test_parse_with_upload_id_sends_form_field(self, client):
        """parse_with_upload_id sends POST with upload_id in form data."""

        def _fake_post(url, data=None, timeout=None):
            assert "/parse" in url
            assert data == {"upload_id": "my-upload-2"}

            class FakeResp:
                status_code = 200

                def json(self):
                    return {
                        "success": True,
                        "data": {"markdown": "# Parsed content"},
                    }

            return FakeResp()

        import requests as _requests_module

        with patch.object(_requests_module, "post", side_effect=_fake_post):
            result = client.parse_with_upload_id("my-upload-2")
            assert result["success"] is True
            assert result["data"]["markdown"] == "# Parsed content"

    def test_parse_upload_file_dry_run(self, client):
        """parse_upload_file with dry_run returns preview dict."""
        client.dry_run = True
        result = client.parse_upload_file("dry-1", "/some/file.pdf")
        assert result["dry_run"] is True
        assert result["method"] == "PUT"
        assert result["file"] == "/some/file.pdf"
        assert "/parse/upload/dry-1" in result["url"]

    def test_parse_with_upload_id_dry_run(self, client):
        """parse_with_upload_id with dry_run returns preview dict."""
        client.dry_run = True
        result = client.parse_with_upload_id("dry-2")
        assert result["dry_run"] is True
        assert result["method"] == "POST"
        assert result["upload_id"] == "dry-2"

    def test_parse_upload_file_api_error(self, client):
        """parse_upload_file raises ApiError on 4xx response."""
        api_error_cls = _cli_ns["ApiError"]

        def _fake_put(url, data=None, headers=None, timeout=None):
            class FakeResp:
                status_code = 404

                def json(self):
                    return {"detail": "Upload not found"}

            return FakeResp()

        import requests as _requests_module

        with patch.object(_requests_module, "put", side_effect=_fake_put):
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(b"test")
                tmp_path = tmp.name
            try:
                with pytest.raises(api_error_cls) as exc_info:
                    client.parse_upload_file("bad-1", tmp_path)
                assert "Upload not found" in str(exc_info.value)
            finally:
                os.unlink(tmp_path)

    def test_parse_with_upload_id_api_error(self, client):
        """parse_with_upload_id raises ApiError on 4xx response."""
        api_error_cls = _cli_ns["ApiError"]

        def _fake_post(url, data=None, timeout=None):
            class FakeResp:
                status_code = 400

                def json(self):
                    return {"detail": "Invalid upload_id"}

            return FakeResp()

        import requests as _requests_module

        with patch.object(_requests_module, "post", side_effect=_fake_post):
            with pytest.raises(api_error_cls) as exc_info:
                client.parse_with_upload_id("bad-2")
            assert "Invalid upload_id" in str(exc_info.value)


# ── cmd_parse_upload handler tests ────────────────────────────────────────────


class TestCmdParseUpload:
    """Tests for the cmd_parse_upload() handler function."""

    def test_cmd_parse_upload_calls_client(self):
        """cmd_parse_upload calls parse_upload_file with correct args."""
        cmd_parse_upload = _cli_ns["cmd_parse_upload"]
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"hello")
            tmp_path = tmp.name
        try:
            mock_args = MagicMock()
            mock_args.upload_id = "up-1"
            mock_args.file = tmp_path
            mock_args.dry_run = False
            mock_client = MagicMock()
            mock_client.dry_run = False
            mock_client.parse_upload_file.return_value = {
                "success": True,
                "upload_id": "up-1",
                "size": 5,
            }
            cmd_parse_upload(mock_client, mock_args)
            mock_client.parse_upload_file.assert_called_once_with("up-1", tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_cmd_parse_upload_json_output(self):
        """cmd_parse_upload with JSON_OUTPUT produces valid JSON."""
        cmd_parse_upload = _cli_ns["cmd_parse_upload"]
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"hello world")
            tmp_path = tmp.name
        try:
            mock_args = MagicMock()
            mock_args.upload_id = "up-json-1"
            mock_args.file = tmp_path
            mock_args.dry_run = False
            mock_client = MagicMock()
            mock_client.dry_run = False
            mock_client.parse_upload_file.return_value = {
                "success": True,
                "upload_id": "up-json-1",
                "size": 11,
            }
            original_json = _cli_ns["JSON_OUTPUT"]
            _cli_ns["JSON_OUTPUT"] = True
            try:
                stdout = _capture_stdout(cmd_parse_upload, mock_client, mock_args)
            finally:
                _cli_ns["JSON_OUTPUT"] = original_json
            parsed = json.loads(stdout.strip())
            assert parsed["success"] is True
            assert parsed["upload_id"] == "up-json-1"
        finally:
            os.unlink(tmp_path)

    def test_cmd_parse_upload_file_not_found(self):
        """cmd_parse_upload exits with error when file does not exist."""
        cmd_parse_upload = _cli_ns["cmd_parse_upload"]
        mock_args = MagicMock()
        mock_args.upload_id = "up-1"
        mock_args.file = "/nonexistent/file.pdf"
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        stderr = _capture_stderr(cmd_parse_upload, mock_client, mock_args)
        assert "File not found" in stderr
        assert "/nonexistent/file.pdf" in stderr

    def test_cmd_parse_upload_dry_run(self):
        """cmd_parse_upload with dry_run emits preview and returns."""
        cmd_parse_upload = _cli_ns["cmd_parse_upload"]
        mock_args = MagicMock()
        mock_args.upload_id = "dry-up-1"
        mock_args.file = "/some/file.pdf"
        mock_args.dry_run = True
        mock_client = MagicMock()
        mock_client.dry_run = True
        stdout = _capture_stdout(cmd_parse_upload, mock_client, mock_args)
        assert "dry-run" in stdout.lower() or "Would upload" in stdout

    def test_cmd_parse_upload_api_error(self):
        """cmd_parse_upload exits cleanly on ApiError."""
        cmd_parse_upload = _cli_ns["cmd_parse_upload"]
        api_error_cls = _cli_ns["ApiError"]
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test")
            tmp_path = tmp.name
        try:
            mock_args = MagicMock()
            mock_args.upload_id = "up-err-1"
            mock_args.file = tmp_path
            mock_args.dry_run = False
            mock_client = MagicMock()
            mock_client.dry_run = False
            mock_client.parse_upload_file.side_effect = api_error_cls(
                "Upload error (500): Internal server error"
            )
            stderr = _capture_stderr(cmd_parse_upload, mock_client, mock_args)
            assert "Error:" in stderr
            assert "Upload error" in stderr
            assert "Traceback" not in stderr
        finally:
            os.unlink(tmp_path)


# ── parse --upload-id flag tests ──────────────────────────────────────────────


class TestCmdParseWithUploadId:
    """Tests for the parse command with --upload-id flag."""

    def test_cmd_parse_with_upload_id_calls_client(self):
        """parse --upload-id calls parse_with_upload_id."""
        cmd_parse = _cli_ns["cmd_parse"]
        mock_args = MagicMock()
        mock_args.upload_id = "pu-1"
        mock_args.filepath = None
        mock_args.output = None
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.parse_with_upload_id.return_value = {
            "success": True,
            "data": {"markdown": "# Hello"},
        }
        stdout = _capture_stdout(cmd_parse, mock_client, mock_args)
        mock_client.parse_with_upload_id.assert_called_once_with("pu-1")
        assert "# Hello" in stdout

    def test_cmd_parse_with_upload_id_json_output(self):
        """parse --upload-id with JSON_OUTPUT produces valid JSON."""
        cmd_parse = _cli_ns["cmd_parse"]
        mock_args = MagicMock()
        mock_args.upload_id = "pu-json-1"
        mock_args.filepath = None
        mock_args.output = None
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.parse_with_upload_id.return_value = {
            "success": True,
            "data": {"markdown": "# Hi"},
        }
        original_json = _cli_ns["JSON_OUTPUT"]
        _cli_ns["JSON_OUTPUT"] = True
        try:
            stdout = _capture_stdout(cmd_parse, mock_client, mock_args)
        finally:
            _cli_ns["JSON_OUTPUT"] = original_json
        parsed = json.loads(stdout.strip())
        assert parsed["success"] is True
        assert parsed["data"]["markdown"] == "# Hi"

    def test_cmd_parse_with_upload_id_api_error(self):
        """parse --upload-id exits cleanly on ApiError."""
        cmd_parse = _cli_ns["cmd_parse"]
        api_error_cls = _cli_ns["ApiError"]
        mock_args = MagicMock()
        mock_args.upload_id = "pu-err-1"
        mock_args.filepath = None
        mock_args.output = None
        mock_args.dry_run = False
        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.parse_with_upload_id.side_effect = api_error_cls(
            "Parse error (404): Upload not found"
        )
        stderr = _capture_stderr(cmd_parse, mock_client, mock_args)
        assert "Error:" in stderr
        assert "Upload not found" in stderr
        assert "Traceback" not in stderr

    def test_cmd_parse_with_upload_id_dry_run(self):
        """parse --upload-id with dry_run emits preview."""
        cmd_parse = _cli_ns["cmd_parse"]
        mock_args = MagicMock()
        mock_args.upload_id = "pu-dry-1"
        mock_args.filepath = None
        mock_args.output = None
        mock_args.dry_run = True
        mock_client = MagicMock()
        mock_client.dry_run = True
        stdout = _capture_stdout(cmd_parse, mock_client, mock_args)
        assert "pu-dry-1" in stdout or "dry-run" in stdout.lower()


# ── parse-upload parser tests ─────────────────────────────────────────────────


class TestParseUploadParser:
    """Tests for the parse-upload subcommand argument parser."""

    def test_help_contains_parse_upload(self):
        """Top-level --help lists parse-upload command."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        from contextlib import redirect_stdout
        from io import StringIO

        stdout = StringIO()
        with pytest.raises(SystemExit):
            with redirect_stdout(stdout):
                parser.parse_args(["--help"])
        help_text = stdout.getvalue()
        assert "parse-upload" in help_text

    def test_parse_upload_args(self):
        """parse-upload parses upload_id and --file correctly."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["parse-upload", "my-id", "--file", "/tmp/report.pdf"])
        assert args.upload_id == "my-id"
        assert args.file == "/tmp/report.pdf"
        assert args.command == "parse-upload"

    def test_parse_upload_file_required(self):
        """parse-upload --file is required."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        from contextlib import redirect_stderr
        from io import StringIO

        stderr = StringIO()
        with pytest.raises(SystemExit):
            with redirect_stderr(stderr):
                parser.parse_args(["parse-upload", "my-id"])
        assert (
            "error" in stderr.getvalue().lower()
            or "required" in stderr.getvalue().lower()
        )

    def test_parse_with_upload_id_flag(self):
        """parse --upload-id is parsed correctly."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        args = parser.parse_args(["parse", "--upload-id", "abc-123"])
        assert args.upload_id == "abc-123"
        assert args.filepath is None

    def test_parse_upload_id_help(self):
        """parse --help shows --upload-id flag."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        from contextlib import redirect_stdout
        from io import StringIO

        stdout = StringIO()
        with pytest.raises(SystemExit):
            with redirect_stdout(stdout):
                parser.parse_args(["parse", "--help"])
        help_text = stdout.getvalue()
        assert "--upload-id" in help_text


# ── cmd_monitor: run handler tests ────────────────────────────────────────────


class TestCmdMonitorRun:
    """Tests for the monitor run subcommand handler."""

    def test_run_calls_run_monitor(self):
        """monitor run calls client.run_monitor() with the correct ID."""
        cmd_monitor = _cli_ns["cmd_monitor"]

        mock_args = MagicMock()
        mock_args.command_action = "run"
        mock_args.id = "mon-abc123"
        mock_args.dry_run = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.run_monitor.return_value = {
            "success": True,
            "id": "mon-abc123",
            "monitor_type": "scrape",
            "url": "http://example.com",
            "schedule": "0 */6 * * *",
            "last_checked": "2026-06-27T12:00:00Z",
            "last_result": "changed",
        }

        cmd_monitor(mock_client, mock_args)

        mock_client.run_monitor.assert_called_once_with("mon-abc123")

    def test_run_json_output(self):
        """monitor run with JSON_OUTPUT produces valid JSON."""
        cmd_monitor = _cli_ns["cmd_monitor"]

        mock_args = MagicMock()
        mock_args.command_action = "run"
        mock_args.id = "mon-xyz789"
        mock_args.dry_run = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.run_monitor.return_value = {
            "success": True,
            "id": "mon-xyz789",
            "monitor_type": "search",
            "search_config": {"query": "latest AI news"},
            "schedule": "0 */6 * * *",
            "last_checked": "2026-06-27T12:00:00Z",
            "last_result": "unchanged",
        }

        original_json = _cli_ns["JSON_OUTPUT"]
        _cli_ns["JSON_OUTPUT"] = True
        try:
            stdout = _capture_stdout(cmd_monitor, mock_client, mock_args)
        finally:
            _cli_ns["JSON_OUTPUT"] = original_json

        output = stdout.strip()
        parsed = json.loads(output)
        assert parsed["id"] == "mon-xyz789"
        assert parsed["monitor_type"] == "search"

    def test_run_api_error_handled(self):
        """ApiError in monitor run exits with clean error message."""
        cmd_monitor = _cli_ns["cmd_monitor"]
        api_error_cls = _cli_ns["ApiError"]

        mock_args = MagicMock()
        mock_args.command_action = "run"
        mock_args.id = "mon-err-1"
        mock_args.dry_run = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.run_monitor.side_effect = api_error_cls(
            "Cannot connect to http://test-server:8080: Connection refused"
        )

        stderr = _capture_stderr(cmd_monitor, mock_client, mock_args)
        assert "Error:" in stderr
        assert "Cannot connect" in stderr
        assert "Traceback" not in stderr

    def test_run_dry_run(self):
        """Dry-run monitor run emits message without calling API."""
        cmd_monitor = _cli_ns["cmd_monitor"]

        mock_args = MagicMock()
        mock_args.command_action = "run"
        mock_args.id = "mon-dry-1"
        mock_args.dry_run = False

        mock_client = MagicMock()
        mock_client.dry_run = True

        cmd_monitor(mock_client, mock_args)


# ── Image support tests ──────────────────────────────────────────────────────


class TestImageSupport:
    """Tests for image format, download, and display features."""

    def test_scrape_format_includes_images(self):
        """scrape --format includes 'images' in choices."""
        scrape_parser = None
        for action in _cli_ns["make_parser"]()._actions:
            if getattr(action, "dest", "") == "command":
                for choice, subparser in getattr(action, "choices", {}).items():
                    if choice == "scrape":
                        scrape_parser = subparser
                        break
        assert scrape_parser is not None
        # Verify --format choice includes "images"
        for action in scrape_parser._actions:
            if "--format" in action.option_strings:
                assert "images" in action.choices
                break

    def test_crawl_format_flag_accepted(self):
        """crawl --format flag passes scrape_options to client's extra kwargs."""
        cmd_crawl = _cli_ns["cmd_crawl"]

        mock_args = MagicMock()
        mock_args.url = "http://example.com"
        mock_args.limit = 10
        mock_args.max_depth = 2
        mock_args.include_paths = None
        mock_args.exclude_paths = None
        mock_args.max_pages = None
        mock_args.ignore_query_parameters = False
        mock_args.no_poll = True
        mock_args.dry_run = False
        mock_args.format = ["markdown", "images"]

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.crawl.return_value = {"success": True, "id": "img-job-1"}

        cmd_crawl(mock_client, mock_args)

        mock_client.crawl.assert_called_once_with(
            url="http://example.com",
            limit=10,
            max_depth=2,
            include_paths=None,
            exclude_paths=None,
            max_pages=None,
            ignore_query_parameters=False,
            scrape_options={"formats": ["markdown", "images"]},
        )

    def test_scrape_with_images_display(self):
        """cmd_scrape displays images when data.images is present."""
        cmd_scrape = _cli_ns["cmd_scrape"]

        mock_args = MagicMock()
        mock_args.url = "http://example.com"
        mock_args.format = ["markdown", "images"]
        mock_args.only_main_content = True
        mock_args.timeout = 30000
        mock_args.contents = None
        mock_args.output = None
        mock_args.dry_run = False
        mock_args.download_images = False

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.scrape.return_value = {
            "success": True,
            "data": {
                "markdown": "# Test Page\n\nContent here.",
                "images": [
                    {
                        "url": "https://example.com/img1.png",
                        "alt": "Test image",
                        "width": 800,
                        "height": 600,
                        "position": 1,
                    },
                    {
                        "url": "https://example.com/img2.jpg",
                        "alt": "",
                        "width": None,
                        "height": None,
                        "position": 2,
                    },
                ],
            },
        }

        stdout = _capture_stdout(cmd_scrape, mock_client, mock_args)
        assert "# Test Page" in stdout
        assert "Images found on page: 2" in stdout
        assert "Test image" in stdout

    def test_agent_include_images_flag(self):
        """agent --include-images passes include_images to create_agent_stream."""
        cmd_agent = _cli_ns["cmd_agent"]

        mock_args = MagicMock()
        mock_args.prompt = "research images"
        mock_args.urls = None
        mock_args.sync = False
        mock_args.no_poll = False
        mock_args.pyramid = False
        mock_args.output_dir = ""
        mock_args.include_images = True
        mock_args.search_type = "deep"

        # Mock a stream response that returns one "done" event
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = [
            "data: "
            + json.dumps(
                {
                    "type": "done",
                    "result": "test result",
                    "sources": [],
                    "latency_ms": 100,
                }
            ),
            "data: [DONE]",
        ]

        mock_client = MagicMock()
        mock_client.dry_run = False
        mock_client.create_agent_stream.return_value = {"_stream": mock_resp}

        cmd_agent(mock_client, mock_args)

        mock_client.create_agent_stream.assert_called_once_with(
            prompt="research images",
            urls=None,
            include_images=True,
            search_type="deep",
        )

    def test_search_type_images_in_choices(self):
        """search --search-type includes 'images' in choices."""
        make_parser = _cli_ns["make_parser"]
        parser = make_parser()
        search_parser = None
        for action in parser._actions:
            if getattr(action, "dest", "") == "command":
                choices = getattr(action, "choices", {})
                if "search" in choices:
                    search_parser = choices["search"]
                    break
        assert search_parser is not None
        for action in search_parser._actions:
            if "--search-type" in action.option_strings:
                assert "images" in action.choices
                break
