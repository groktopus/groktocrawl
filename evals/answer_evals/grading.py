"""Deterministic, stdlib-only graders for the grounded-answer eval harness.

Each grader returns a structured verdict: ``{"pass": bool, "message": str,
"detail": dict}``. Graders are deliberately mechanical — there is no
keyword-as-grounding proxy: citation support binds a claim to an exact citation
index, an exact source URL, and an evidence span inside that source's pinned
content.

The ``result`` operand is the harness-normalized observed outcome::

    {
        "protocol": {"status": int, "success": bool},
        "answer": str,
        "sources": [{"url": str, "title": str, "relevance": str}],
        "citations": [{"index": int, "url": str}],
        "error": str | None,
    }
"""

from __future__ import annotations

import re
from collections.abc import Callable

# Stable identities for provenance (issue #570).
GRADER_VERSIONS: dict[str, str] = {
    "check_protocol": "v1",
    "check_sources_present": "v1",
    "check_citation_shape": "v2",
    "check_citation_support": "v1",
    "check_required_claims": "v1",
    "check_prohibited_claims": "v1",
    "check_abstention": "v1",
    "check_citation_integrity": "v2",
}

_MARKER = re.compile(r"\[(\d+)\]")

INSUFFICIENT_PHRASES = (
    "unable to find",
    "no relevant web pages",
    "insufficient",
    "not enough",
    "cannot answer",
    "could not find",
)

CONTRADICTORY_PHRASES = (
    "contradict",
    "conflict",
    "conflicting",
    "inconsistent",
    "disagree",
    "conflicting sources",
    "cannot provide a confident answer",
    "cannot give a confident",
)


def normalize(text: str) -> str:
    """Lowercase and collapse all whitespace for deterministic matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _verdict(passed: bool, message: str, detail: dict | None = None) -> dict:
    return {"pass": passed, "message": message, "detail": detail or {}}


def check_protocol(result: dict, case: dict) -> dict:
    """The pipeline returned the per-case expected HTTP protocol outcome."""
    observed = result.get("protocol") or {}
    status = observed.get("status")
    success = observed.get("success")
    expected = (case.get("expected") or {}).get("protocol") or {}
    exp_status = expected.get("status")
    exp_success = expected.get("success", True)

    if isinstance(exp_status, str) and exp_status == "5xx":
        status_ok = isinstance(status, int) and 500 <= status < 600
    else:
        status_ok = status == exp_status
    success_ok = success is exp_success
    passed = status_ok and success_ok
    return _verdict(
        passed,
        (
            f"protocol {'matches' if passed else 'mismatch'}: "
            f"observed status={status} success={success}, "
            f"expected status={exp_status} success={exp_success}"
        ),
        {
            "observed_status": status,
            "observed_success": success,
            "expected_status": exp_status,
            "expected_success": exp_success,
        },
    )


def check_sources_present(result: dict, case: dict) -> dict:
    """Sources are non-empty, unique, and within ``allowable_source_urls``."""
    sources = result.get("sources") or []
    allowable = set((case.get("expected") or {}).get("allowable_source_urls") or [])
    if not sources:
        return _verdict(False, "no sources returned", {"source_count": 0})
    urls = [s.get("url", "") for s in sources]
    duplicates = sorted({u for u in urls if urls.count(u) > 1})
    if duplicates:
        return _verdict(
            False, f"duplicate source URLs: {duplicates}", {"duplicates": duplicates}
        )
    disallowed = sorted({u for u in urls if u not in allowable})
    if disallowed:
        return _verdict(
            False,
            f"sources outside allowlist: {disallowed}",
            {"disallowed": disallowed},
        )
    return _verdict(
        True, f"{len(sources)} allowed, unique sources", {"source_count": len(sources)}
    )


def _citation_prose(answer: str) -> str:
    """Mask Markdown literals independently of the runtime citation parser."""
    lines = answer.splitlines(keepends=True)
    fence: tuple[str, int] | None = None
    paragraph_open = False
    visible: list[str] = []
    fence_line = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*?)(?:\r?\n)?$")
    for line in lines:
        content = line.rstrip("\r\n")
        delimiter = fence_line.match(content)
        if fence is not None:
            visible.append(" " * len(line))
            if (
                delimiter
                and delimiter[2][0] == fence[0]
                and len(delimiter[2]) >= fence[1]
                and not delimiter[3].strip()
            ):
                fence = None
            continue
        if delimiter:
            character = delimiter[2][0]
            info = delimiter[3]
            # CommonMark forbids backticks in a backtick fence's info string.
            if character == "~" or "`" not in info:
                fence = (character, len(delimiter[2]))
                paragraph_open = False
                visible.append(" " * len(line))
                continue
        if not content.strip():
            visible.append(line)
            paragraph_open = False
            continue
        if (line.startswith("\t") or line.startswith("    ")) and not paragraph_open:
            visible.append(" " * len(line))
            continue
        visible.append(line)
        paragraph_open = True

    text = list("".join(visible))
    # Escaped punctuation is opaque. Masking each slash run leaves the next
    # character visible only when the number of backslashes is even.
    position = 0
    while position < len(text):
        if text[position] != "\\":
            position += 1
            continue
        end = position
        while end < len(text) and text[end] == "\\":
            end += 1
        for index in range(position, end):
            text[index] = " "
        if (end - position) % 2 == 1 and end < len(text):
            text[end] = " "
            end += 1
        position = end

    # Mask exact-length inline backtick spans, without treating an unmatched
    # delimiter as a reason to hide the rest of the answer.
    position = 0
    while position < len(text):
        if text[position] != "`":
            position += 1
            continue
        run_end = position
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        run_length = run_end - position
        search = run_end
        closing: tuple[int, int] | None = None
        while search < len(text):
            if text[search] != "`":
                search += 1
                continue
            candidate_end = search
            while candidate_end < len(text) and text[candidate_end] == "`":
                candidate_end += 1
            if candidate_end - search == run_length:
                closing = (search, candidate_end)
                break
            search = candidate_end
        if closing is None:
            position = run_end
            continue
        if re.search(r"\n[ \t\r]*\n", "".join(text[run_end : closing[0]])):
            position = run_end
            continue
        for index in range(position, closing[1]):
            text[index] = " "
        position = closing[1]
    return "".join(text)


def _balanced_bracket_end(text: str, start: int) -> int | None:
    """Return the matching bracket while respecting nested labels and escapes."""
    depth = 1
    position = start + 1
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text[position] == "[":
            depth += 1
        elif text[position] == "]":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return None


def _markdown_destination_end(text: str, start: int) -> int | None:
    """Return the closing paren for a simple or angle-delimited destination."""
    if start >= len(text) or text[start] != "(":
        return None
    if start + 1 < len(text) and text[start + 1] == "<":
        angle_end = text.find(">", start + 2)
        if angle_end < 0 or angle_end + 1 >= len(text):
            return None
        return angle_end + 1 if text[angle_end + 1] == ")" else None
    depth = 1
    position = start + 1
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text[position] == "(":
            depth += 1
        elif text[position] == ")":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return None


def _numeric_citation_prefix(label: str) -> bool:
    return bool(re.match(r"\s*[+\-]?\d", label))


def _citation_violations(result: dict, case: dict) -> list[dict]:
    """Validate canonical prose and metadata without calling production code.

    Numeric groups/ranges are noncanonical final output. Numeric-looking bare
    arrays are citations; literal examples must be escaped or in code.
    """
    sources = result.get("sources") or []
    citations = result.get("citations") or []
    compact = case.get("citation_style") == "compact"
    text = _citation_prose(result.get("answer") or "")
    bad: list[dict] = []
    observed: set[int] = set()
    pos = 0
    while pos < len(text):
        if text[pos] != "[":
            pos += 1
            continue
        image = pos > 0 and text[pos - 1] == "!"
        end = _balanced_bracket_end(text, pos)
        if end is None:
            if _numeric_citation_prefix(text[pos + 1 :]):
                bad.append({"reason": "incomplete_numeric_marker"})
            pos += 1
            continue

        label = text[pos + 1 : end]
        numeric = _numeric_citation_prefix(label)
        next_pos = end + 1
        url = None
        if next_pos < len(text) and text[next_pos] == "(":
            destination_end = _markdown_destination_end(text, next_pos)
            if destination_end is None:
                if numeric and not image:
                    bad.append({"reason": "incomplete_citation_url"})
                pos = next_pos + 1
                continue
            destination = text[next_pos + 1 : destination_end]
            if destination.startswith("<") and destination.endswith(">"):
                destination = destination[1:-1]
            url = destination
            next_pos = destination_end + 1

        # Ordinary labels (including embedded numeric examples) and images are
        # Markdown, not source claims. Skip their reference label and target.
        if image or not numeric:
            if next_pos < len(text) and text[next_pos] == "[":
                reference_end = _balanced_bracket_end(text, next_pos)
                if reference_end is not None:
                    next_pos = reference_end + 1
            pos = next_pos
            continue

        if not re.fullmatch(r"[1-9][0-9]*", label):
            bad.append({"reason": "noncanonical_numeric_marker", "marker": label})
            pos = next_pos
            continue

        # Compare decimal strings before int conversion so pathological input
        # cannot escape the grader as Python's max-digits exception.
        maximum = str(len(sources))
        if len(label) > len(maximum) or (
            len(label) == len(maximum) and label > maximum
        ):
            bad.append({"index": None, "reason": "out_of_range"})
            pos = next_pos
            continue
        index = int(label)
        observed.add(index)
        if not 1 <= index <= len(sources):
            bad.append({"index": index, "reason": "out_of_range"})
        elif url is not None and url != sources[index - 1].get("url"):
            bad.append({"index": index, "reason": "citation_url_mismatch"})
        if compact and url is None:
            bad.append({"index": index, "reason": "missing_compact_url"})
        pos = next_pos

    declared: set[int] = set()
    for citation in citations:
        index = citation.get("index")
        if type(index) is not int or not 1 <= index <= len(sources):
            bad.append({"reason": "invalid_metadata_index", "index": index})
            continue
        if index in declared:
            bad.append({"reason": "duplicate_metadata", "index": index})
        declared.add(index)
        if citation.get("url") != sources[index - 1].get("url"):
            bad.append({"reason": "metadata_url_mismatch", "index": index})
        if index not in observed:
            bad.append({"reason": "extraneous_metadata", "index": index})
    if not compact:
        for index in sorted(observed - declared):
            bad.append({"reason": "no_citation_returned", "index": index})
    return bad


def check_citation_shape(result: dict, case: dict) -> dict:
    """Canonical markers and metadata agree with the fixed source ordering."""
    bad = _citation_violations(result, case)
    return _verdict(
        not bad,
        f"{len(bad)} citation shape violation(s)" if bad else "citation shape valid",
        {"violations": bad},
    )


def check_citation_support(result: dict, case: dict) -> dict:
    """Each declared claim is bound to its exact source and pinned content.

    PASS requires, per declared ``expected.citations[].{claim_id, answer_span,
    evidence_span, source_url, citation_index}``:

    (a) the claim's citation index resolves to the exact ``source_url``,
    (b) the normalized ``answer_span`` appears in the answer, and
    (c) the normalized ``evidence_span`` appears in that source's pinned
        content (``case["source_content"][source_url]``).

    A deliberately mis-cited negative case must fail here.
    """
    expected_citations = (case.get("expected") or {}).get("citations") or []
    if not expected_citations:
        return _verdict(True, "no declared citations to support-check")
    result_citations = result.get("citations") or []
    result_by_index = {c.get("index"): c.get("url", "") for c in result_citations}
    sources = result.get("sources") or []
    answer = normalize(result.get("answer") or "")
    pinned = case.get("source_content") or {}
    failures: list[dict] = []

    for claim in expected_citations:
        claim_id = claim.get("claim_id", "")
        index = claim.get("citation_index")
        source_url = claim.get("source_url", "")
        answer_span = normalize(claim.get("answer_span", ""))
        evidence_span = normalize(claim.get("evidence_span", ""))

        resolved = result_by_index.get(index)
        index_ok = resolved == source_url
        if not index_ok:
            failures.append(
                {
                    "claim_id": claim_id,
                    "criterion": "citation_index_to_source_url",
                    "index": index,
                    "resolved_url": resolved,
                    "source_url": source_url,
                }
            )

        answer_ok = bool(answer_span) and answer_span in answer
        if not answer_ok:
            failures.append(
                {
                    "claim_id": claim_id,
                    "criterion": "answer_span_present",
                    "answer_span": claim.get("answer_span"),
                }
            )

        pinned_content = pinned.get(source_url)
        evidence_ok = (
            bool(evidence_span)
            and pinned_content is not None
            and evidence_span in normalize(pinned_content)
        )
        if not evidence_ok:
            failures.append(
                {
                    "claim_id": claim_id,
                    "criterion": "evidence_span_in_pinned_content",
                    "evidence_span": claim.get("evidence_span"),
                    "source_url": source_url,
                    "pinned": pinned_content is not None,
                }
            )
        if index_ok and answer_ok and evidence_ok:
            # Ensure the resolved URL is actually among the returned sources.
            if source_url not in {s.get("url", "") for s in sources}:
                failures.append(
                    {
                        "claim_id": claim_id,
                        "criterion": "source_returned",
                        "source_url": source_url,
                    }
                )

    if failures:
        return _verdict(
            False,
            f"{len(failures)} citation-support failure(s)",
            {"failures": failures},
        )
    return _verdict(
        True, f"{len(expected_citations)} claim(s) supported by pinned content"
    )


def check_required_claims(result: dict, case: dict) -> dict:
    answer = normalize(result.get("answer") or "")
    required = (case.get("expected") or {}).get("required_claims") or []
    missing = [claim for claim in required if normalize(claim) not in answer]
    if missing:
        return _verdict(
            False, f"missing required claims: {missing}", {"missing": missing}
        )
    return _verdict(True, f"{len(required)} required claim(s) present")


def check_prohibited_claims(result: dict, case: dict) -> dict:
    answer = normalize(result.get("answer") or "")
    prohibited = (case.get("expected") or {}).get("prohibited_claims") or []
    found = [claim for claim in prohibited if normalize(claim) in answer]
    if found:
        return _verdict(False, f"prohibited claims present: {found}", {"found": found})
    return _verdict(True, f"{len(prohibited)} prohibited claim(s) absent")


def check_abstention(result: dict, case: dict) -> dict:
    expected = (case.get("expected") or {}).get("abstain_expected", False)
    qualifier = (case.get("expected") or {}).get("abstain_qualifier")
    answer = normalize(result.get("answer") or "")
    empty = not answer

    def _contains(phrases: tuple[str, ...]) -> bool:
        return any(phrase in answer for phrase in phrases)

    if expected:
        if qualifier == "insufficient":
            ok = empty or _contains(INSUFFICIENT_PHRASES)
            label = "insufficient-evidence abstention"
        elif qualifier == "contradictory":
            ok = _contains(CONTRADICTORY_PHRASES)
            label = "contradictory-evidence qualification/abstention"
        else:
            ok = (
                empty
                or _contains(INSUFFICIENT_PHRASES)
                or _contains(CONTRADICTORY_PHRASES)
            )
            label = "abstention"
        return _verdict(
            ok,
            f"{label} {'detected' if ok else 'not detected'}",
            {"empty": empty, "qualifier": qualifier},
        )

    if empty:
        return _verdict(
            False, "abstention not expected but answer is empty", {"empty": True}
        )
    if _contains(INSUFFICIENT_PHRASES) or _contains(CONTRADICTORY_PHRASES):
        return _verdict(
            False,
            "abstention not expected but answer contains an abstention phrase",
            {"empty": False},
        )
    return _verdict(True, "answer present and not an abstention")


def check_citation_integrity(result: dict, case: dict) -> dict:
    """Every prose citation resolves, without hidden or fabricated metadata."""
    bad = _citation_violations(result, case)
    return _verdict(
        not bad,
        f"{len(bad)} integrity violation(s)" if bad else "all citation markers resolve",
        {"violations": bad},
    )


GRADERS: dict[str, Callable[[dict, dict], dict]] = {
    "check_protocol": check_protocol,
    "check_sources_present": check_sources_present,
    "check_citation_shape": check_citation_shape,
    "check_citation_support": check_citation_support,
    "check_required_claims": check_required_claims,
    "check_prohibited_claims": check_prohibited_claims,
    "check_abstention": check_abstention,
    "check_citation_integrity": check_citation_integrity,
}


def applicable_graders(case: dict) -> list[str]:
    """Return the ordered grader ids that apply to *case*.

    For a non-200 expected protocol only ``check_protocol`` applies (a
    provider-failure outcome has no answer/sources to grade). For an
    abstention case the citation/source graders would vacuously fail on the
    intentionally empty result, so they are excluded; required/prohibited
    claim lists are empty for abstention cases and pass trivially.
    """
    expected = case.get("expected") or {}
    protocol = expected.get("protocol") or {}
    if protocol.get("status") not in (200, "2xx"):
        return ["check_protocol"]
    if expected.get("abstain_expected"):
        return [
            "check_protocol",
            "check_abstention",
            "check_required_claims",
            "check_prohibited_claims",
        ]
    return [
        "check_protocol",
        "check_sources_present",
        "check_citation_shape",
        "check_citation_support",
        "check_required_claims",
        "check_prohibited_claims",
        "check_abstention",
        "check_citation_integrity",
    ]
