"""Exact, deterministic source passage preparation."""

import pytest
from agent.experimental.passage_preparation import (
    MAX_PASSAGE_CHARACTERS,
    RetainedSourceText,
    prepare_source_passages,
)


def test_passages_reconstruct_every_source_byte_in_order() -> None:
    first = "A" * (MAX_PASSAGE_CHARACTERS + 7)
    second = "Unicode: 🐙\nExact ending."
    passages = prepare_source_passages(
        (
            RetainedSourceText("snapshot-a", first),
            RetainedSourceText("snapshot-b", second),
        )
    )

    reconstructed = {
        snapshot_id: "".join(
            item.quote for item in passages if item.snapshot_id == snapshot_id
        )
        for snapshot_id in ("snapshot-a", "snapshot-b")
    }
    assert reconstructed == {"snapshot-a": first, "snapshot-b": second}
    assert [(item.start, item.end) for item in passages[:2]] == [
        (0, MAX_PASSAGE_CHARACTERS),
        (MAX_PASSAGE_CHARACTERS, len(first)),
    ]


def test_preparation_is_byte_for_byte_deterministic() -> None:
    sources = (RetainedSourceText("snapshot-a", "same exact text"),)

    assert prepare_source_passages(sources) == prepare_source_passages(sources)


def test_large_sources_fail_instead_of_silent_truncation() -> None:
    sources = (
        RetainedSourceText("snapshot-a", "a" * 70_000),
        RetainedSourceText("snapshot-b", "b" * 50_001),
    )

    with pytest.raises(ValueError, match="byte budget"):
        prepare_source_passages(sources)


@pytest.mark.parametrize(
    "sources, message",
    [
        ((), "source count"),
        (
            (
                RetainedSourceText("duplicate", "first"),
                RetainedSourceText("duplicate", "second"),
            ),
            "identities must be distinct",
        ),
        ((RetainedSourceText("snapshot-a", ""),), "identity and text"),
    ],
)
def test_invalid_source_sets_fail_closed(
    sources: tuple[RetainedSourceText, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_source_passages(sources)
