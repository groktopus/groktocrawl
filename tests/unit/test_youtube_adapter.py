"""Regression tests for YouTube transcript language selection."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "scraper-svc")

from scraper.adapters.base import AdapterContext
from scraper.adapters.youtube import (
    YouTubeAdapter,
    _fetch_transcript,
    _TranscriptFetch,
    _YouTubeRequestGate,
)

from tests.fixtures.youtube_transcript_twin import YouTubeTranscriptTwin


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
    from scraper.adapters import youtube

    gate = _YouTubeRequestGate()
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
        return asyncio.run(_fetch_transcript("PPM2ODdo2t8"))


def test_native_english_is_preferred():
    translated = _Track("fi", translatable=True)
    native = _Track("en")

    result = _run([translated, native])

    assert result == _TranscriptFetch("usable transcript", "en")
    assert translated.fetch_calls == 0


def test_translatable_non_english_track_is_translated_to_english():
    translated_track = _Track("fi", translatable=True)
    translated = _Track("en")
    translated_track._translated = translated

    result = _run([translated_track])

    assert result == _TranscriptFetch("usable transcript", "fi", "en")
    assert translated.fetch_calls == 1


def test_captionless_video_remains_unavailable():
    assert _run([]) is None


def test_untranslatable_non_english_track_remains_unavailable():
    assert _run([_Track("fi")]) is None


def test_failed_preferred_track_falls_back_to_next_usable_track():
    failed = _Track("en", fetch_error=RuntimeError("blocked"))
    fallback = _Track("en")

    result = _run([failed, fallback])

    assert result == _TranscriptFetch("usable transcript", "en")


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
            new=AsyncMock(return_value=_TranscriptFetch("translated text", "fi", "en")),
        ),
    ):
        result = asyncio.run(
            YouTubeAdapter().scrape(
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
    result = asyncio.run(_fetch_transcript(twin.video_id))
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
    gate = _YouTubeRequestGate()
    gate.cooldown_seconds = 60
    gate.mark_rate_limited()
    try:
        gate.wait()
    except Exception as exc:
        assert type(exc).__name__ == "_YouTubeCooldownError"
    else:
        raise AssertionError("cooldown must block new acquisition attempts")
