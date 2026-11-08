"""Deterministic citation parsing and incremental stream parity tests."""

import pytest
from agent.exceptions import CitationValidationError
from agent.models import CitationStyle
from agent.research.citations import CitationNormalizer, _apply_citation_style

SOURCES = [
    {"url": "https://example.test/one"},
    {"url": "https://example.test/two"},
]


def test_grouped_markers_normalize_and_resolve_to_frozen_sources():
    sources = [dict(source) for source in SOURCES]
    normalizer = CitationNormalizer(sources, CitationStyle.inline)
    first = normalizer.feed("Claim [1, ")
    sources[0]["url"] = "https://evil.example/changed"
    second = normalizer.feed("2] is supported.")
    tail = normalizer.finish("Claim [1, 2] is supported.")

    assert first == "Claim "
    assert first + second + tail == "Claim [1][2] is supported."
    assert normalizer.citations == [
        {"index": 1, "url": "https://example.test/one"},
        {"index": 2, "url": "https://example.test/two"},
    ]


def test_grouped_markers_expand_with_compact_source_urls():
    result, citations = _apply_citation_style(
        "Claim [1, 2].", SOURCES, CitationStyle.compact
    )

    assert result == (
        "Claim [1](https://example.test/one)[2](https://example.test/two)."
    )
    assert citations == [
        {"index": 1, "url": "https://example.test/one"},
        {"index": 2, "url": "https://example.test/two"},
    ]


def test_streaming_marker_waits_for_complete_reference_then_matches_done():
    normalizer = CitationNormalizer(SOURCES, CitationStyle.compact)
    first = normalizer.feed("Claim [")
    second = normalizer.feed("1] is supported.")
    tail = normalizer.finish("Claim [1] is supported.")

    assert first == "Claim "
    assert first + second + tail == "Claim [1](https://example.test/one) is supported."


@pytest.mark.parametrize(
    "answer",
    [
        "Claim [0].",
        "Claim [01].",
        "Claim [-1].",
        "Claim [3].",
        "Claim [1-2].",
        "Claim [1,].",
        "Claim [1, 3].",
        "Claim [1](https://wrong.example/).",
        "Claim [1, 2](https://example.test/one).",
        "Claim [1",
        "Claim [1](unfinished",
    ],
)
def test_ambiguous_or_unresolvable_markers_fail_closed(answer):
    with pytest.raises(CitationValidationError) as exc:
        _apply_citation_style(answer, SOURCES, CitationStyle.inline)
    assert exc.value.error_code == "CITATION_INVALID"


def test_valid_source_link_with_parentheses_is_preserved_in_compact_style():
    sources = [{"url": "https://example.test/path_(detail)"}]
    answer = "Claim [1](https://example.test/path_(detail))."

    assert _apply_citation_style(answer, sources, CitationStyle.compact) == (
        answer,
        [{"index": 1, "url": "https://example.test/path_(detail)"}],
    )


def test_markdown_literals_and_ordinary_links_are_not_citations():
    answer = (
        "Literal `[999]` and escaped \\[999].\n"
        "```python\nx = [999]\n```\n"
        "~~~~\n[999]\n~~~\n[999]\n~~~~\n"
        "    [999]\n"
        "![999](https://example.test/image)\n"
        "[ordinary label](https://example.test/[999])\n"
        "[ordinary reference][999]\n"
        "[an example [999]](https://example.test)\n"
        "Actual claim [1]."
    )
    normalized, citations = _apply_citation_style(answer, SOURCES, CitationStyle.inline)

    assert normalized == answer
    assert citations == [{"index": 1, "url": "https://example.test/one"}]


def test_unmarked_abstention_with_no_sources_is_valid():
    answer = "I could not find relevant sources to answer this question."

    assert _apply_citation_style(answer, [], CitationStyle.inline) == (answer, [])


def test_repeated_markers_deduplicate_metadata_and_keep_reference_order():
    answer = "First [2], then [1], then [2] again."

    normalized, citations = _apply_citation_style(answer, SOURCES, CitationStyle.inline)

    assert normalized == answer
    assert citations == [
        {"index": 1, "url": "https://example.test/one"},
        {"index": 2, "url": "https://example.test/two"},
    ]


def test_stream_requires_exact_provider_token_parity():
    normalizer = CitationNormalizer(SOURCES, CitationStyle.inline)
    assert normalizer.feed("Claim [1].") == "Claim [1]."

    with pytest.raises(CitationValidationError, match="did not match"):
        normalizer.finish("Claim [1] changed.")


def test_unmarked_stream_requires_a_provider_done_event():
    normalizer = CitationNormalizer(SOURCES, CitationStyle.inline)
    assert normalizer.feed("No citations.") == "No citations."

    with pytest.raises(CitationValidationError, match="without a final completion"):
        normalizer.finish(None)


@pytest.mark.parametrize(
    ("answer", "style", "expected"),
    [
        (
            "Claim [1, 2] now.",
            CitationStyle.inline,
            "Claim [1][2] now.",
        ),
        (
            "Claim ![999](https://example.test/image) [1].",
            CitationStyle.inline,
            "Claim ![999](https://example.test/image) [1].",
        ),
        (
            "[ordinary [999] [999]](https://example.test) Claim [1].",
            CitationStyle.inline,
            "[ordinary [999] [999]](https://example.test) Claim [1].",
        ),
        (
            "```[999]``` Claim [1].",
            CitationStyle.inline,
            "```[999]``` Claim [1].",
        ),
        (
            "    [999]\nClaim [1].",
            CitationStyle.inline,
            "    [999]\nClaim [1].",
        ),
    ],
)
def test_two_chunk_splits_and_character_feeds_match_sync(answer, style, expected):
    sync, expected_citations = _apply_citation_style(answer, SOURCES, style)
    assert sync == expected
    for split in range(len(answer) + 1):
        normalizer = CitationNormalizer(SOURCES, style)
        chunks = [answer[:split], answer[split:]]
        rendered = "".join(normalizer.feed(chunk) for chunk in chunks)
        rendered += normalizer.finish(answer)
        assert rendered == sync, f"mismatch at split {split}"
        assert normalizer.citations == expected_citations

    normalizer = CitationNormalizer(SOURCES, style)
    rendered = "".join(normalizer.feed(character) for character in answer)
    rendered += normalizer.finish(answer)
    assert rendered == sync
    assert normalizer.citations == expected_citations


def test_malformed_outer_label_cannot_hide_numeric_citation():
    with pytest.raises(CitationValidationError):
        _apply_citation_style(
            "See [draft text and claim [999].", SOURCES, CitationStyle.inline
        )


def test_extremely_long_numeric_marker_is_machine_readable_error():
    answer = f"Claim [{'9' * 4301}]."
    with pytest.raises(CitationValidationError) as exc:
        _apply_citation_style(answer, SOURCES, CitationStyle.inline)
    assert exc.value.error_code == "CITATION_INVALID"


def test_unbalanced_compact_url_destination_is_idempotent():
    sources = [{"url": "https://example.test/a)b"}]
    answer, citations = _apply_citation_style(
        "Claim [1].", sources, CitationStyle.compact
    )
    assert answer == "Claim [1](<https://example.test/a)b>)."
    assert _apply_citation_style(answer, sources, CitationStyle.compact) == (
        answer,
        citations,
    )


def test_unbalanced_compact_destination_matches_every_split():
    sources = [{"url": "https://example.test/a)b"}]
    answer = "Claim [1]."
    expected = "Claim [1](<https://example.test/a)b>)."
    for split in range(len(answer) + 1):
        normalizer = CitationNormalizer(sources, CitationStyle.compact)
        rendered = normalizer.feed(answer[:split]) + normalizer.feed(answer[split:])
        rendered += normalizer.finish(answer)
        assert rendered == expected, f"mismatch at split {split}"
        assert normalizer.citations == [{"index": 1, "url": sources[0]["url"]}]


@pytest.mark.parametrize(
    "answer",
    [
        "[ordinary](https://example.test/unclosed and claim [999].",
        "[ordinary][unclosed and claim [999].",
        "Claim text\n    supported [999].",
        "unmatched `\n\nClaim [999]\n\n`",
    ],
)
def test_malformed_markdown_cannot_hide_prose_citation(answer):
    with pytest.raises(CitationValidationError):
        _apply_citation_style(answer, SOURCES, CitationStyle.inline)


def test_malformed_markup_is_rejected_across_every_chunk_boundary():
    answers = [
        "[ordinary](https://example.test/unclosed and claim [999].",
        "[ordinary][unclosed and claim [999].",
        "Claim text\n    supported [999].",
        "unmatched `\n\nClaim [999]\n\n`",
    ]
    for answer in answers:
        for split in range(len(answer) + 1):
            normalizer = CitationNormalizer(SOURCES, CitationStyle.inline)
            try:
                normalizer.feed(answer[:split])
                normalizer.feed(answer[split:])
                normalizer.finish(answer)
            except CitationValidationError:
                continue
            pytest.fail(f"accepted malformed citation at split {split}: {answer!r}")

        normalizer = CitationNormalizer(SOURCES, CitationStyle.inline)
        try:
            for character in answer:
                normalizer.feed(character)
            normalizer.finish(answer)
        except CitationValidationError:
            continue
        pytest.fail(f"accepted malformed citation in character feed: {answer!r}")


def test_open_ordinary_destination_scan_advances_incrementally():
    normalizer = CitationNormalizer(SOURCES, CitationStyle.inline)
    answer = "[ordinary](" + "x" * 16_384
    for offset in range(0, len(answer), 4):
        normalizer.feed(answer[offset : offset + 4])
    state = normalizer._construct_scans[("destination", len("[ordinary]"))]
    assert state["position"] == len(answer)


def test_angle_destination_prefix_and_closer_are_chunk_safe():
    url = "https://example.test/a)b"
    sources = [{"url": url}]
    answer = f"Claim [1](<{url}>)."
    for split in range(len(answer) + 1):
        normalizer = CitationNormalizer(sources, CitationStyle.compact)
        rendered = normalizer.feed(answer[:split]) + normalizer.feed(answer[split:])
        rendered += normalizer.finish(answer)
        assert rendered == answer, f"mismatch at split {split}"

    normalizer = CitationNormalizer(sources, CitationStyle.compact)
    rendered = "".join(normalizer.feed(character) for character in answer)
    rendered += normalizer.finish(answer)
    assert rendered == answer


def test_inline_replay_clears_scanner_cursors():
    answer = "[a]xx [b] `foo\n\n[1999]."
    source = [{"url": "https://example.test/one"}]
    with pytest.raises(CitationValidationError):
        _apply_citation_style(answer, source, CitationStyle.inline)

    for split in range(len(answer) + 1):
        normalizer = CitationNormalizer(source, CitationStyle.inline)
        with pytest.raises(CitationValidationError):
            normalizer.feed(answer[:split])
            normalizer.feed(answer[split:])
            normalizer.finish(answer)


def test_many_blank_line_inline_restarts_do_not_recurse():
    answer = "`x\n\n" * 1100
    assert _apply_citation_style(answer, [], CitationStyle.inline) == (answer, [])
