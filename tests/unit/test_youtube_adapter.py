"""Regression tests for YouTube transcript language selection."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "scraper-svc")

from scraper.adapters import youtube
from scraper.adapters.base import AdapterContext

from tests.fixtures.youtube_transcript_twin import YouTubeTranscriptTwin


def test_transcript_api_uses_gateway_pinned_requests_session(monkeypatch):
    import requests
    import youtube_transcript_api

    monkeypatch.setenv("SCRAPER_CAPTURE_EGRESS_PROXY_URL", "http://egress.example:8080")
    sessions = []
    api_calls = []

    class Session:
        def __init__(self):
            self.trust_env = True
            self.proxies = {}
            self.closed = False
            sessions.append(self)

        def close(self):
            self.closed = True

    class Transcript:
        language_code = "en"
        is_translatable = False

        def fetch(self):
            return [SimpleNamespace(text="captions through the gateway")]

    class TranscriptApi:
        def __init__(self, **kwargs):
            api_calls.append(kwargs)
            self.http_client = kwargs["http_client"]

        def list(self, video_id):
            assert video_id == "abcdefghijk"
            return [Transcript()]

    monkeypatch.setattr(requests, "Session", Session)
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", TranscriptApi)
    monkeypatch.setattr(youtube._YOUTUBE_GATE, "wait", lambda *_args, **_kwargs: None)

    result = asyncio.run(youtube._fetch_transcript("abcdefghijk"))

    assert result is not None and result.text == "captions through the gateway"
    assert len(sessions) == 1
    assert sessions[0].trust_env is False
    assert sessions[0].proxies == {
        "http": "http://egress.example:8080",
        "https": "http://egress.example:8080",
    }
    assert sessions[0].closed is True
    assert api_calls == [{"http_client": sessions[0]}]


def test_transcript_api_keeps_default_constructor_in_compatibility_mode(monkeypatch):
    import youtube_transcript_api

    monkeypatch.delenv("SCRAPER_CAPTURE_EGRESS_PROXY_URL", raising=False)
    api_calls = []

    class Transcript:
        language_code = "en"
        is_translatable = False

        def fetch(self):
            return [SimpleNamespace(text="default transcript behavior")]

    class TranscriptApi:
        def __init__(self, **kwargs):
            api_calls.append(kwargs)

        def list(self, _video_id):
            return [Transcript()]

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", TranscriptApi)
    monkeypatch.setattr(youtube._YOUTUBE_GATE, "wait", lambda *_args, **_kwargs: None)

    result = asyncio.run(youtube._fetch_transcript("abcdefghijk"))

    assert result is not None and result.text == "default transcript behavior"
    assert api_calls == [{}]


def test_ytdlp_source_requests_use_explicit_gateway_proxy(monkeypatch):
    monkeypatch.setenv("SCRAPER_CAPTURE_EGRESS_PROXY_URL", "http://egress.example:8080")
    captured = []

    class YoutubeDL:
        def __init__(self, options):
            self.options = options
            captured.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, *, download):
            assert url == "https://www.youtube.com/watch?v=abcdefghijk"
            assert download is True
            output = self.options["outtmpl"].replace("%(ext)s", "en.vtt")
            with open(output, "w", encoding="utf-8") as output_file:
                output_file.write(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n\n"
                    "caption fetched through yt-dlp\n"
                )
            return {"automatic_captions": {}}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    monkeypatch.setattr(youtube._YOUTUBE_GATE, "wait", lambda *_args, **_kwargs: None)

    result = asyncio.run(youtube._fetch_transcript_via_ytdlp("abcdefghijk"))

    assert result is not None and result.text == "caption fetched through yt-dlp"
    assert captured[0]["proxy"] == "http://egress.example:8080"


class _Fetched:
    def __init__(self, *texts):
        self.items = [SimpleNamespace(text=text) for text in texts]

    def __iter__(self):
        return iter(self.items)


class _Track:
    def __init__(
        self,
        language_code,
        *,
        translatable=False,
        translated=None,
        fetch_error=None,
    ):
        self.language_code = language_code
        self.is_translatable = translatable
        self._translated = translated
        self._fetch_error = fetch_error
        self.fetch_calls = 0

    def translate(self, language_code):
        assert language_code == "en"
        return self._translated

    def fetch(self):
        self.fetch_calls += 1
        if self._fetch_error:
            raise self._fetch_error
        return _Fetched("usable transcript")


def _run(tracks):
    gate = youtube._YouTubeRequestGate()
    gate.min_interval = 0
    gate.max_interval = 0
    gate.request_interval = 0
    gate.subtitle_interval = 0
    gate._cooldown_until = 0
    api = SimpleNamespace(list=lambda _video_id: iter(tracks))
    with (
        patch.object(youtube, "_YOUTUBE_GATE", gate),
        patch.dict(
            sys.modules,
            {
                "youtube_transcript_api": SimpleNamespace(
                    YouTubeTranscriptApi=lambda: api
                )
            },
        ),
    ):
        return asyncio.run(youtube._fetch_transcript("PPM2ODdo2t8"))


def test_native_english_is_preferred():
    translated = _Track("fi", translatable=True)
    native = _Track("en")

    result = _run([translated, native])

    assert result == youtube._TranscriptFetch("usable transcript", "en")
    assert translated.fetch_calls == 0


def test_translatable_non_english_track_is_translated_to_english():
    translated_track = _Track("fi", translatable=True)
    translated = _Track("en")
    translated_track._translated = translated

    result = _run([translated_track])

    assert result == youtube._TranscriptFetch("usable transcript", "fi", "en")
    assert translated.fetch_calls == 1


def test_captionless_video_remains_unavailable():
    assert _run([]) is None


def test_untranslatable_non_english_track_remains_unavailable():
    assert _run([_Track("fi")]) is None


def test_failed_preferred_track_falls_back_to_next_usable_track():
    failed = _Track("en", fetch_error=RuntimeError("blocked"))
    fallback = _Track("en")

    result = _run([failed, fallback])

    assert result == youtube._TranscriptFetch("usable transcript", "en")


def test_scrape_exposes_translation_provenance():
    with (
        patch(
            "scraper.adapters.youtube._fetch_oembed",
            new=AsyncMock(return_value={"title": "Video", "author_name": "Channel"}),
        ),
        patch(
            "scraper.adapters.youtube._fetch_description",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "scraper.adapters.youtube._fetch_transcript",
            new=AsyncMock(
                return_value=youtube._TranscriptFetch("translated text", "fi", "en")
            ),
        ),
    ):
        result = asyncio.run(
            youtube.YouTubeAdapter().scrape(
                "https://www.youtube.com/watch?v=PPM2ODdo2t8", AdapterContext()
            )
        )

    assert result.success is True
    assert result.source == "youtube-transcript-api"
    assert result.metadata["transcript_language"] == "fi"
    assert result.metadata["transcript_translated_to"] == "en"
    assert "translated text" in result.markdown


def test_vtt_to_text_removes_timing_and_repeated_cues():
    from scraper.adapters.youtube import _vtt_to_text

    assert (
        _vtt_to_text(
            "WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n\n00:01.000 --> 00:02.000\nHello\nworld"
        )
        == "Hello world"
    )


def test_caption_twin_recovers_translation_omitted_by_transcript_api(monkeypatch):
    twin = YouTubeTranscriptTwin(monkeypatch)
    twin.install()
    result = asyncio.run(youtube._fetch_transcript(twin.video_id))
    assert result is not None
    assert result.text == "Hello from translated caption"
    assert result.language == "fi"
    assert result.translated_to == "en"
    assert twin.list_calls == 1
    assert twin.ytdlp_calls == 1
    assert twin.ytdlp_options["sleep_interval_requests"] == 0.75
    assert twin.ytdlp_options["sleep_interval_subtitles"] == 5
    assert twin.ytdlp_options["retries"] == 1


def test_gate_enforces_cooldown_without_retrying_or_falling_back():
    gate = youtube._YouTubeRequestGate()
    gate.cooldown_seconds = 60
    gate.mark_rate_limited()
    try:
        gate.wait()
    except Exception as exc:
        assert type(exc).__name__ == "_YouTubeCooldownError"
    else:
        raise AssertionError("cooldown must block new acquisition attempts")


def test_ytdlp_keeps_default_proxy_behavior_in_compatibility_mode(monkeypatch):
    monkeypatch.delenv("SCRAPER_CAPTURE_EGRESS_PROXY_URL", raising=False)
    captured = []

    class YoutubeDL:
        def __init__(self, options):
            captured.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, *, download):
            assert download is True
            output = captured[-1]["outtmpl"].replace("%(ext)s", "en.vtt")
            with open(output, "w", encoding="utf-8") as output_file:
                output_file.write(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n\n"
                    "default yt-dlp behavior\n"
                )
            return {"automatic_captions": {}}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    monkeypatch.setattr(youtube._YOUTUBE_GATE, "wait", lambda *_args, **_kwargs: None)

    result = asyncio.run(youtube._fetch_transcript_via_ytdlp("abcdefghijk"))

    assert result is not None and result.text == "default yt-dlp behavior"
    assert "proxy" not in captured[0]
