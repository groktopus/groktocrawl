"""Citation style helpers and deterministic citation validation."""

import logging
import re
import time
from typing import Any, NoReturn

from ..exceptions import CitationValidationError
from ..metrics import METRICS
from ..models import CitationStyle

logger = logging.getLogger(__name__)

_GROUPED_MARKER = re.compile(r"([1-9][0-9]*)(?:[ \t]*,[ \t]*([1-9][0-9]*))*\Z")
_CANONICAL_MARKER = re.compile(r"[1-9][0-9]*\Z")
_NUMERIC_PREFIX = re.compile(r"\s*[+-]?[0-9]")


def _build_answer_user_prompt(query: str, citation_style: Any) -> str:
    """Build the user prompt for the answer pipeline, adjusting citation
    instructions based on the requested citation style."""
    if citation_style == CitationStyle.compact:
        cite_instr = "Cite sources using [1](url), [2](url), etc. — include the URL directly in the citation marker."
    else:
        cite_instr = "Cite sources using [1], [2], etc. corresponding to the source numbers above."

    return (
        f"Answer the following question using ONLY the sources provided above.\n\n"
        f"Question: {query}\n\n"
        f"{cite_instr}"
    )


class CitationNormalizer:
    """Normalize and validate numeric citations as text arrives.

    The source URLs are copied at construction time so the citation identity
    cannot drift while a response is streaming. Only an unambiguous numeric
    marker or group is interpreted as a citation; code, escaped markers, and
    ordinary Markdown labels are passed through unchanged.
    """

    def __init__(self, source_map: list[dict[str, Any]], citation_style: Any):
        self._source_urls = tuple(
            source.get("url") if isinstance(source, dict) else None
            for source in source_map
        )
        self._style = citation_style
        self._style_name = getattr(citation_style, "value", str(citation_style))
        if self._style_name not in {"inline", "compact"}:
            self._style = CitationStyle.inline
            self._style_name = "inline"
        self._pending = ""
        self._raw_parts: list[str] = []
        self._used_indices: set[int] = set()
        self._was_normalized = False
        self._finished = False
        self._recorded = False
        self._processing_seconds = 0.0
        self._line_prefix_active = True
        self._line_prefix_width = 0
        self._line_prefix_spaces_only = True
        self._paragraph_open = False
        self._line_has_nonspace = False
        self._last_raw_char: str | None = None
        self._trailing_backslashes = 0
        self._last_bang_escaped = False
        self._in_indented_code_line = False
        self._in_fence = False
        self._fence_character: str | None = None
        self._fence_minimum = 0
        self._fence_line_state = "indent"
        self._fence_line_buffer: list[str] = []
        self._fence_indent = 0
        self._fence_run = 0
        self._in_inline_code = False
        self._inline_delimiter = 0
        self._inline_buffer: list[str] = []
        self._inline_opening = ""
        self._inline_run = 0
        self._inline_newline_pending = False
        self._inline_context_before: tuple[Any, ...] | None = None
        self._literal_backtick_skip: int | None = None
        self._construct_scans: dict[tuple[str, int], dict[str, Any]] = {}

    @property
    def citations(self) -> list[dict[str, Any]]:
        """Return stable, deduplicated metadata for the cited source indices."""
        return [
            {"index": index, "url": self._source_urls[index - 1]}
            for index in sorted(self._used_indices)
        ]

    def feed(self, content: str) -> str:
        """Consume one provider chunk and return only citation-safe text."""
        if self._finished:
            raise RuntimeError("citation normalizer is already finished")
        if not isinstance(content, str):
            self._record("invalid")
            raise CitationValidationError("LLM output contained invalid citations")
        self._raw_parts.append(content)
        self._pending += content
        try:
            return self._drain(final=False)
        except CitationValidationError:
            self._record("invalid")
            raise

    def finish(self, provider_full_content: str | None = None) -> str:
        """Flush the final safe suffix and require provider/token parity."""
        if self._finished:
            raise RuntimeError("citation normalizer is already finished")
        self._finished = True
        try:
            raw_content = "".join(self._raw_parts)
            if not isinstance(provider_full_content, str):
                raise CitationValidationError(
                    "LLM stream ended without a final completion"
                )
            if provider_full_content != raw_content:
                raise CitationValidationError(
                    "LLM token stream did not match its final completion"
                )
            remainder = self._drain(final=True)
            if self._pending:
                raise CitationValidationError("LLM output contained invalid citations")
            self._record("normalized" if self._was_normalized else "valid")
            return remainder
        except CitationValidationError:
            self._record("invalid")
            raise

    def _record(self, outcome: str) -> None:
        if self._recorded:
            return
        self._recorded = True
        labels = {"style": self._style_name, "outcome": outcome}
        METRICS.counter(
            "groktocrawl_citation_validation_total",
            "Citation validation outcomes by requested style",
            ["style", "outcome"],
        ).inc(labels)
        METRICS.histogram(
            "groktocrawl_citation_validation_seconds",
            "Citation parser processing time by requested style and outcome",
            ["style", "outcome"],
        ).observe(labels, self._processing_seconds)

    def _invalid(
        self, detail: str = "LLM output contained invalid citations"
    ) -> NoReturn:
        raise CitationValidationError(detail)

    @staticmethod
    def _run_end(text: str, position: int, character: str) -> int:
        end = position
        while end < len(text) and text[end] == character:
            end += 1
        return end

    def _scan_construct(self, text: str, start: int, kind: str) -> int | None:
        """Incrementally scan one open Markdown construct across chunks."""
        key = (kind, start)
        state = self._construct_scans.get(key)
        if state is None:
            has_destination_prefix = start + 1 < len(text)
            angle = (
                kind == "destination"
                and has_destination_prefix
                and text[start + 1] == "<"
            )
            state = {
                "position": start + (2 if angle else 1),
                "depth": 1,
                "escaped": False,
                "angle": angle,
                "waiting_for_destination_prefix": (
                    kind == "destination" and not has_destination_prefix
                ),
                "result": None,
            }
            self._construct_scans[key] = state
        if state["result"] is not None:
            return state["result"]
        if state.get("waiting_for_destination_prefix"):
            if start + 1 >= len(text):
                return None
            state["angle"] = text[start + 1] == "<"
            state["position"] = start + (2 if state["angle"] else 1)
            state["waiting_for_destination_prefix"] = False

        position = state["position"]
        while position < len(text):
            character = text[position]
            if state["escaped"]:
                state["escaped"] = False
                position += 1
                continue
            if character == "\\":
                state["escaped"] = True
                position += 1
                continue
            if kind == "bracket":
                if character == "[":
                    state["depth"] += 1
                elif character == "]":
                    state["depth"] -= 1
                    if state["depth"] == 0:
                        state["position"] = position
                        state["result"] = position
                        return position
            elif state["angle"]:
                if character == ">":
                    if position + 1 < len(text):
                        if text[position + 1] == ")":
                            state["position"] = position
                            state["result"] = position + 1
                            return position + 1
                        state["result"] = -1
                        return None
                    state["position"] = position
                    return None
            elif character == "(":
                state["depth"] += 1
            elif character == ")":
                state["depth"] -= 1
                if state["depth"] == 0:
                    state["position"] = position
                    state["result"] = position
                    return position
            position += 1

        state["position"] = position
        return None

    def _bracket_end(self, text: str, start: int) -> int | None:
        result = self._scan_construct(text, start, "bracket")
        return result if result is not None and result >= 0 else None

    def _parenthesized_end(self, text: str, start: int) -> int | None:
        if start >= len(text) or text[start] != "(":
            return None
        result = self._scan_construct(text, start, "destination")
        return result if result is not None and result >= 0 else None

    def _rebase_construct_scans(self, offset: int) -> None:
        rebased = {}
        for (kind, start), state in self._construct_scans.items():
            if start < offset:
                continue
            state["position"] = max(state["position"] - offset, start + 1 - offset)
            if state["result"] is not None and state["result"] >= 0:
                state["result"] -= offset
            rebased[(kind, start - offset)] = state
        self._construct_scans = rebased

    @staticmethod
    def _numeric_looking(label: str) -> bool:
        return bool(_NUMERIC_PREFIX.match(label))

    @staticmethod
    def _numeric_prefix(label: str) -> bool:
        stripped = label.lstrip()
        return not stripped or stripped[0].isdigit() or stripped[0] in "+-"

    def _source_index(self, digits: str) -> int:
        source_count = len(self._source_urls)
        largest = str(source_count)
        if (
            not digits
            or len(digits) > len(largest)
            or (len(digits) == len(largest) and digits > largest)
        ):
            self._invalid()
        index = int(digits)
        if not 1 <= index <= source_count:
            self._invalid()
        return index

    def _parse_indices(self, label: str) -> list[int] | None:
        if _CANONICAL_MARKER.fullmatch(label):
            return [self._source_index(label)]
        if _GROUPED_MARKER.fullmatch(label):
            return [self._source_index(part.strip()) for part in label.split(",")]
        if self._numeric_looking(label):
            self._invalid()
        return None

    @staticmethod
    def _format_compact_destination(url: str) -> str:
        depth = 0
        unbalanced = False
        for character in url:
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    unbalanced = True
                else:
                    depth -= 1
        if depth:
            unbalanced = True
        if "<" in url or ">" in url:
            raise CitationValidationError("LLM output contained invalid citations")
        return f"<{url}>" if unbalanced else url

    @staticmethod
    def _extract_link_url(text: str, start: int, end: int) -> str:
        destination = text[start + 1 : end]
        if destination.startswith("<") and destination.endswith(">"):
            return destination[1:-1]
        return destination

    def _normalize_marker(
        self,
        label: str,
        link_url: str | None,
        original: str,
    ) -> str:
        indices = self._parse_indices(label)
        if indices is None:
            return f"[{label}]"
        if link_url is not None and len(indices) != 1:
            self._invalid()
        for index in indices:
            source_url = self._source_urls[index - 1]
            if not isinstance(source_url, str) or not source_url:
                self._invalid()
            if link_url is not None and link_url != source_url:
                self._invalid()
            self._used_indices.add(index)

        if self._style_name == "compact":
            compact_markers = []
            for index in indices:
                source_url = self._source_urls[index - 1]
                if not isinstance(source_url, str) or not source_url:
                    self._invalid()
                compact_markers.append(
                    f"[{index}]({self._format_compact_destination(source_url)})"
                )
            normalized = "".join(compact_markers)
        else:
            normalized = "".join(f"[{index}]" for index in indices)
        if normalized != original:
            self._was_normalized = True
        return normalized

    def _advance_raw_context(self, segment: str, *, paragraph: bool = True) -> None:
        for character in segment:
            if character == "\n":
                self._line_prefix_active = True
                self._line_prefix_width = 0
                self._line_prefix_spaces_only = True
                if paragraph:
                    if not self._line_has_nonspace:
                        self._paragraph_open = False
                    self._line_has_nonspace = False
            elif self._line_prefix_active:
                if character == " ":
                    self._line_prefix_width = min(self._line_prefix_width + 1, 5)
                elif character == "\t":
                    self._line_prefix_width = min(
                        ((self._line_prefix_width // 4) + 1) * 4, 5
                    )
                    self._line_prefix_spaces_only = False
                else:
                    self._line_prefix_active = False
                    self._line_prefix_spaces_only = False

            if paragraph and character not in "\n\r \t":
                self._paragraph_open = True
                self._line_has_nonspace = True

            if character == "\\":
                self._trailing_backslashes += 1
                self._last_bang_escaped = False
            else:
                self._last_bang_escaped = (
                    character == "!" and self._trailing_backslashes % 2 == 1
                )
                self._trailing_backslashes = 0
            self._last_raw_char = character

    def _drain(self, *, final: bool) -> str:
        started = time.monotonic()
        text = self._pending
        output: list[str] = []
        position = 0

        def consume(end: int, *, paragraph: bool = True) -> None:
            nonlocal position
            self._advance_raw_context(text[position:end], paragraph=paragraph)
            position = end

        def emit(end: int, *, paragraph: bool = True) -> None:
            output.append(text[position:end])
            consume(end, paragraph=paragraph)

        try:
            while position < len(text):
                if self._in_fence:
                    character = text[position]
                    state = self._fence_line_state
                    if state == "opaque":
                        output.append(character)
                        consume(position + 1, paragraph=False)
                        if character == "\n":
                            self._fence_line_state = "indent"
                            self._fence_indent = 0
                        continue

                    if state == "indent":
                        if character == " " and self._fence_indent < 4:
                            self._fence_line_buffer.append(character)
                            self._fence_indent += 1
                            consume(position + 1, paragraph=False)
                            if self._fence_indent == 4:
                                output.append("".join(self._fence_line_buffer))
                                self._fence_line_buffer.clear()
                                self._fence_line_state = "opaque"
                            continue
                        if character == "\t":
                            self._fence_line_buffer.append(character)
                            consume(position + 1, paragraph=False)
                            output.append("".join(self._fence_line_buffer))
                            self._fence_line_buffer.clear()
                            self._fence_line_state = "opaque"
                            continue
                        if character == "\n":
                            output.append("".join(self._fence_line_buffer) + character)
                            self._fence_line_buffer.clear()
                            self._fence_indent = 0
                            consume(position + 1, paragraph=False)
                            continue
                        if character == self._fence_character:
                            self._fence_line_buffer.append(character)
                            self._fence_run = 1
                            self._fence_line_state = "run"
                            consume(position + 1, paragraph=False)
                            continue
                        if self._fence_line_buffer:
                            output.append("".join(self._fence_line_buffer))
                            self._fence_line_buffer.clear()
                        if character == "\n":
                            output.append(character)
                            consume(position + 1, paragraph=False)
                            self._fence_indent = 0
                        else:
                            output.append(character)
                            consume(position + 1, paragraph=False)
                            self._fence_line_state = "opaque"
                        continue

                    if state == "run":
                        if character == self._fence_character:
                            self._fence_line_buffer.append(character)
                            self._fence_run += 1
                            consume(position + 1, paragraph=False)
                            continue
                        if (
                            self._fence_run >= self._fence_minimum
                            and character in " \t\r"
                        ):
                            self._fence_line_buffer.append(character)
                            self._fence_line_state = "suffix"
                            consume(position + 1, paragraph=False)
                            continue
                        if self._fence_run >= self._fence_minimum and character == "\n":
                            self._fence_line_buffer.append(character)
                            output.append("".join(self._fence_line_buffer))
                            self._fence_line_buffer.clear()
                            self._in_fence = False
                            self._fence_character = None
                            self._fence_line_state = "indent"
                            self._fence_indent = 0
                            self._fence_run = 0
                            consume(position + 1, paragraph=False)
                            continue
                        output.append("".join(self._fence_line_buffer) + character)
                        self._fence_line_buffer.clear()
                        self._fence_line_state = "opaque"
                        self._fence_run = 0
                        consume(position + 1, paragraph=False)
                        if character == "\n":
                            self._fence_line_state = "indent"
                            self._fence_indent = 0
                        continue

                    if state == "suffix":
                        if character in " \t\r":
                            self._fence_line_buffer.append(character)
                            consume(position + 1, paragraph=False)
                            continue
                        if character == "\n":
                            self._fence_line_buffer.append(character)
                            output.append("".join(self._fence_line_buffer))
                            self._fence_line_buffer.clear()
                            self._in_fence = False
                            self._fence_character = None
                            self._fence_line_state = "indent"
                            self._fence_indent = 0
                            self._fence_run = 0
                            consume(position + 1, paragraph=False)
                            continue
                        output.append("".join(self._fence_line_buffer) + character)
                        self._fence_line_buffer.clear()
                        self._fence_line_state = "opaque"
                        self._fence_run = 0
                        consume(position + 1, paragraph=False)
                        if character == "\n":
                            self._fence_line_state = "indent"
                            self._fence_indent = 0
                        continue

                if self._in_indented_code_line:
                    newline = text.find("\n", position)
                    if newline < 0:
                        emit(len(text), paragraph=False)
                        if final:
                            self._in_indented_code_line = False
                        continue
                    emit(newline + 1, paragraph=False)
                    self._paragraph_open = False
                    self._line_has_nonspace = False
                    self._in_indented_code_line = False
                    continue

                if self._in_inline_code:
                    character = text[position]
                    if character == "`":
                        self._inline_run += 1
                        consume(position + 1)
                        continue
                    if self._inline_run:
                        ticks = "`" * self._inline_run
                        if self._inline_run == self._inline_delimiter:
                            self._inline_buffer.append(ticks)
                            output.append("".join(self._inline_buffer))
                            self._inline_buffer.clear()
                            self._inline_opening = ""
                            self._inline_delimiter = 0
                            self._inline_run = 0
                            self._inline_newline_pending = False
                            self._inline_context_before = None
                            self._in_inline_code = False
                            continue
                        self._inline_buffer.append(ticks)
                        self._inline_run = 0
                    self._inline_buffer.append(character)
                    blank_line = character == "\n" and self._inline_newline_pending
                    if character == "\n":
                        self._inline_newline_pending = True
                    elif self._inline_newline_pending and character in " \t\r":
                        pass
                    else:
                        self._inline_newline_pending = False
                    consume(position + 1)
                    if blank_line:
                        replay = (
                            "".join(self._inline_buffer)
                            + "`" * self._inline_run
                            + text[position:]
                        )
                        self._construct_scans.clear()
                        if self._inline_context_before is not None:
                            (
                                self._line_prefix_active,
                                self._line_prefix_width,
                                self._line_prefix_spaces_only,
                                self._paragraph_open,
                                self._line_has_nonspace,
                                self._last_raw_char,
                                self._trailing_backslashes,
                                self._last_bang_escaped,
                            ) = self._inline_context_before
                        self._in_inline_code = False
                        self._inline_delimiter = 0
                        self._inline_buffer = []
                        self._inline_opening = ""
                        self._inline_run = 0
                        self._inline_newline_pending = False
                        self._inline_context_before = None
                        self._literal_backtick_skip = len(replay) - len(
                            replay.lstrip("`")
                        )
                        text = replay
                        position = 0
                        continue
                    continue

                character = text[position]

                if self._line_prefix_active and character in " \t":
                    if character == "\t":
                        if self._paragraph_open:
                            emit(position + 1)
                            continue
                        output.append(character)
                        consume(position + 1, paragraph=False)
                        self._in_indented_code_line = True
                        continue
                    run_end = position
                    while run_end < len(text) and text[run_end] == " ":
                        run_end += 1
                    total_indent = self._line_prefix_width + (run_end - position)
                    if total_indent >= 4 and not self._paragraph_open:
                        output.append(text[position:run_end])
                        consume(run_end, paragraph=False)
                        self._in_indented_code_line = True
                        continue
                    if run_end == len(text) and not final:
                        break
                    emit(run_end)
                    continue

                if character == "\\":
                    if position + 1 >= len(text) and not final:
                        break
                    if position + 1 < len(text):
                        emit(position + 2)
                    else:
                        emit(position + 1)
                    continue

                if character == "`":
                    run_end = self._run_end(text, position, "`")
                    run_length = run_end - position
                    if run_end == len(text) and not final:
                        break
                    if self._literal_backtick_skip == run_length:
                        self._literal_backtick_skip = None
                        emit(run_end)
                        continue
                    is_fence_position = (
                        self._line_prefix_active
                        and self._line_prefix_spaces_only
                        and self._line_prefix_width <= 3
                    )
                    if is_fence_position and run_length >= 3:
                        line_end = text.find("\n", run_end)
                        header_end = line_end if line_end >= 0 else len(text)
                        info = text[run_end:header_end].rstrip("\r")
                        if "`" not in info:
                            if line_end < 0 and not final:
                                break
                            opener_end = line_end + 1 if line_end >= 0 else len(text)
                            emit(opener_end)
                            self._in_fence = True
                            self._fence_character = "`"
                            self._fence_minimum = run_length
                            self._fence_line_state = "indent"
                            self._fence_line_buffer.clear()
                            self._fence_indent = 0
                            self._fence_run = 0
                            self._paragraph_open = False
                            self._line_has_nonspace = False
                            continue
                    if (
                        is_fence_position
                        and run_length < 3
                        and run_end == len(text)
                        and not final
                    ):
                        break
                    self._inline_context_before = (
                        self._line_prefix_active,
                        self._line_prefix_width,
                        self._line_prefix_spaces_only,
                        self._paragraph_open,
                        self._line_has_nonspace,
                        self._last_raw_char,
                        self._trailing_backslashes,
                        self._last_bang_escaped,
                    )
                    self._in_inline_code = True
                    self._inline_delimiter = run_length
                    self._inline_opening = text[position:run_end]
                    self._inline_buffer = [self._inline_opening]
                    self._inline_run = 0
                    self._inline_newline_pending = False
                    consume(run_end)
                    continue

                if character == "~":
                    run_end = self._run_end(text, position, "~")
                    run_length = run_end - position
                    is_fence_position = (
                        self._line_prefix_active
                        and self._line_prefix_spaces_only
                        and self._line_prefix_width <= 3
                    )
                    if is_fence_position and run_length >= 3:
                        line_end = text.find("\n", run_end)
                        if line_end < 0 and not final:
                            break
                        opener_end = line_end + 1 if line_end >= 0 else len(text)
                        emit(opener_end)
                        self._in_fence = True
                        self._fence_character = "~"
                        self._fence_minimum = run_length
                        self._fence_line_state = "indent"
                        self._fence_line_buffer.clear()
                        self._fence_indent = 0
                        self._fence_run = 0
                        self._paragraph_open = False
                        self._line_has_nonspace = False
                        continue
                    if is_fence_position and run_end == len(text) and not final:
                        break

                if character == "[":
                    image = self._last_raw_char == "!" and not self._last_bang_escaped
                    closing = self._bracket_end(text, position)
                    if closing is None:
                        suffix = text[position + 1 :]
                        if not final:
                            break
                        if self._numeric_looking(suffix):
                            self._invalid(
                                "LLM output ended with an incomplete citation"
                            )
                        emit(position + 1)
                        continue

                    label = text[position + 1 : closing]
                    indices = None if image else self._parse_indices(label)
                    after_label = closing + 1
                    link_url = None
                    link_end = None
                    if after_label < len(text) and text[after_label] == "(":
                        link_end = self._parenthesized_end(text, after_label)
                        if link_end is None:
                            if not final:
                                break
                            if indices is not None or self._numeric_looking(label):
                                self._invalid(
                                    "LLM output ended with an incomplete citation link"
                                )
                            emit(after_label)
                            continue
                        link_url = self._extract_link_url(text, after_label, link_end)
                    elif after_label == len(text) and not final:
                        break

                    if indices is not None:
                        marker_end = (
                            link_end + 1 if link_end is not None else after_label
                        )
                        original = text[position:marker_end]
                        normalized = self._normalize_marker(label, link_url, original)
                        output.append(normalized)
                        consume(marker_end)
                        continue

                    ordinary_end = link_end if link_end is not None else closing
                    if (
                        link_end is None
                        and after_label < len(text)
                        and text[after_label] == "["
                    ):
                        reference_end = self._bracket_end(text, after_label)
                        if reference_end is None and not final:
                            break
                        if reference_end is not None:
                            ordinary_end = reference_end
                        elif final:
                            emit(after_label)
                            continue
                    output.append(text[position : ordinary_end + 1])
                    consume(ordinary_end + 1)
                    continue

                emit(position + 1)

            if final and self._in_fence and self._fence_line_buffer:
                is_close = self._fence_line_state == "suffix" or (
                    self._fence_line_state == "run"
                    and self._fence_run >= self._fence_minimum
                )
                output.append("".join(self._fence_line_buffer))
                self._fence_line_buffer.clear()
                if is_close:
                    self._in_fence = False
                    self._fence_character = None
                    self._fence_line_state = "indent"
                    self._fence_indent = 0
                    self._fence_run = 0

            if final and self._in_inline_code:
                if self._inline_run == self._inline_delimiter:
                    self._inline_buffer.append("`" * self._inline_run)
                    output.append("".join(self._inline_buffer))
                    self._inline_buffer.clear()
                    self._in_inline_code = False
                    self._inline_delimiter = 0
                    self._inline_opening = ""
                    self._inline_run = 0
                else:
                    self._inline_buffer.append("`" * self._inline_run)
                    raw_inline = "".join(self._inline_buffer)
                    body = raw_inline[len(self._inline_opening) :]
                    if re.search(r"\[\s*[+-]?[0-9]", body):
                        self._invalid("LLM output has an unfinished code delimiter")
                    output.append(raw_inline)
                    self._inline_buffer.clear()
                    self._in_inline_code = False
                    self._inline_delimiter = 0
                    self._inline_opening = ""
                    self._inline_run = 0
                    self._inline_newline_pending = False
                    self._inline_context_before = None

            self._pending = text[position:]
            self._rebase_construct_scans(position)
            if final and self._pending:
                self._invalid()
            return "".join(output)
        finally:
            self._processing_seconds += time.monotonic() - started


def _apply_citation_style(
    answer: str,
    source_map: list[dict[str, str]],
    citation_style: Any,
) -> tuple[str, list[dict]]:
    """Normalize and validate citations against the fixed source ordering."""
    normalizer = CitationNormalizer(source_map, citation_style)
    prefix = normalizer.feed(answer)
    suffix = normalizer.finish(answer)
    return prefix + suffix, normalizer.citations
