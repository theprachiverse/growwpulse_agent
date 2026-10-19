"""PII scrubbing module for app reviews (Task 2.2).

Detects and redacts sensitive Indian and global PII:
0. Device / advertising IDs (UUIDs)
1. Email (before UPI)
2. UPI handle (after Email)
3. Aadhaar number (before generic account number)
4. PAN (Permanent Account Number)
5. Indian Phone numbers (with false-positive guards for prices, dates, versions)
6. IFSC codes
7. URLs
8. Bank Account Numbers / Generic Long IDs
9. Order / ticket / reference IDs
10. Optional Presidio PERSON entity scrubbing
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

# Regex recognizers ordered strictly to prevent overlapping match conflicts
PATTERNS: Sequence[tuple[str, re.Pattern[str]]] = [
    # 0. Device / advertising IDs (UUID format). Runs first so digit-only UUID
    # segments are not picked up as phone or Aadhaar numbers.
    (
        "DEVICE_ID",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
    ),
    # 1. Email (matched before UPI)
    (
        "EMAIL",
        re.compile(
            r"\b[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+\b",
            re.IGNORECASE,
        ),
    ),
    # 2. UPI handle (after email has replaced standard domains)
    (
        "UPI",
        re.compile(
            r"\b[A-Za-z0-9.\-_]{2,}@[A-Za-z]{2,}\b",
            re.IGNORECASE,
        ),
    ),
    # 2b. +91 phone numbers written without separators (+919876543210). Must run before
    # Aadhaar, whose 12-digit pattern would otherwise swallow "919876543210".
    (
        "PHONE",
        re.compile(r"(?<![\w.])\+91[\-\s]?[6-9]\d{9}(?!\d)"),
    ),
    # 3. Aadhaar number (12 digits, spaced 4-4-4 or continuous; before generic account numbers)
    (
        "AADHAAR",
        re.compile(
            r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}\b|\b\d{12}\b",
        ),
    ),
    # 4. Indian PAN: 5 letters, 4 digits, 1 letter
    (
        "PAN",
        re.compile(
            r"\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b",
        ),
    ),
    # 5. Indian Phone numbers: 10 digits starting with 6-9, optional +91/0 prefix.
    # Lookbehind guards against currency/dates; lookahead allows sentence periods.
    (
        "PHONE",
        re.compile(
            r"(?<![\u20b9$€£\w./-])(?:\+91[\-\s]?|0)?[6-9]\d{2}(?:[\-\s]?\d{2}[\-\s]?\d{5}|[\-\s]?\d{3}[\-\s]?\d{4}|[\-\s]?\d{7})(?![A-Za-z\d]|(?:\.\d))",
        ),
    ),
    # 6. IFSC Code: 4 letters, 0, 6 alphanumeric chars
    (
        "IFSC",
        re.compile(
            r"\b[A-Za-z]{4}0[A-Za-z0-9]{6}\b",
        ),
    ),
    # 7. URLs
    (
        "URL",
        re.compile(
            r"https?://[^\s<>\"']+|www\.[^\s<>\"']+",
            re.IGNORECASE,
        ),
    ),
    # 8. Generic bank account numbers / long IDs (9-18 digits)
    (
        "ACCOUNT_NUMBER",
        re.compile(
            r"\b\d{9,18}\b",
        ),
    ),
    # 9a. Order / ticket / reference IDs introduced by a label, e.g. "Order ID GRW2410AB93",
    # "ticket #8812345". Only the ID itself (the `pii` group) is replaced; the label stays.
    (
        "ID",
        re.compile(
            r"\b(?:order|ticket|txn|transaction|complaint|ref(?:erence)?|request|case)"
            r"\s*(?:id|no\.?|number|#)\s*(?:is\s*)?[:#\-]?\s*"
            r"(?P<pii>(?=[A-Za-z\-]*\d)[A-Za-z0-9\-]{4,})",
            re.IGNORECASE,
        ),
    ),
    # 9b. Unlabelled long tokens mixing letters and digits (12+ chars), e.g. "GRW2410AB93XZ".
    # F&O contract symbols such as NIFTY24OCT24500CE (digit followed by CE/PE/FUT) are kept,
    # because they carry product signal, not identity.
    (
        "ID",
        re.compile(
            r"\b(?=(?:[A-Za-z]*\d){4})(?=\d*[A-Za-z])"
            r"(?![A-Za-z0-9]*\d(?:CE|PE|FUT)\b)"
            r"[A-Za-z0-9]{12,}\b",
            re.IGNORECASE,
        ),
    ),
]


def _substitute(pattern: re.Pattern[str], placeholder: str, text: str) -> str:
    """Replace matches with the placeholder.

    If the pattern has a named group `pii`, only that group is replaced, so labels
    such as "Order ID" survive and the sentence still reads naturally.
    """
    if "pii" not in pattern.groupindex:
        return pattern.sub(placeholder, text)

    def _replace(m: re.Match[str]) -> str:
        whole = m.group(0)
        start = m.start("pii") - m.start()
        end = m.end("pii") - m.start()
        return whole[:start] + placeholder + whole[end:]

    return pattern.sub(_replace, text)


def scrub_pii(text: str, enable_presidio: bool = False) -> tuple[str, list[str]]:
    """Scrub PII from text using ordered regexes and optional Presidio analyzer.

    Returns:
        tuple[str, list[str]]: The scrubbed text with typed placeholders,
        and a deduplicated list of detected PII types (no sensitive values).
    """
    if not text:
        return "", []

    scrubbed = text
    detected_types: list[str] = []

    for pii_type, pattern in PATTERNS:
        if pattern.search(scrubbed):
            scrubbed = _substitute(pattern, f"[{pii_type}]", scrubbed)
            if pii_type not in detected_types:
                detected_types.append(pii_type)

    if enable_presidio:
        scrubbed, person_found = _scrub_presidio_person(scrubbed)
        if person_found and "PERSON" not in detected_types:
            detected_types.append("PERSON")

    return scrubbed, detected_types


class PresidioUnavailableError(RuntimeError):
    """Raised when name scrubbing is enabled but Presidio cannot run.

    Failing loudly is deliberate: silently skipping name scrubbing would let
    reviewer and staff names reach the LLM and the published pulse.
    """


@lru_cache(maxsize=1)
def _presidio_engines() -> tuple[Any, Any, Any]:
    """Build the Presidio analyzer and anonymizer once (loading spaCy is slow)."""
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig
    except ImportError as exc:
        raise PresidioUnavailableError(
            "privacy.enable_presidio is true but Presidio is not installed. Install it with "
            "`uv pip install presidio-analyzer presidio-anonymizer` and "
            "`uv run python -m spacy download en_core_web_lg`, or set "
            "privacy.enable_presidio: false in config/config.yaml."
        ) from exc
    try:
        analyzer = AnalyzerEngine()
    except Exception as exc:  # e.g. spaCy model missing
        raise PresidioUnavailableError(f"Presidio analyzer failed to start: {exc}") from exc
    return analyzer, AnonymizerEngine(), OperatorConfig


def _scrub_presidio_person(text: str) -> tuple[str, bool]:
    """Replace person names with [PERSON] using Presidio. Raises if Presidio is unavailable."""
    analyzer, anonymizer, operator_config = _presidio_engines()
    results = analyzer.analyze(text=text, entities=["PERSON"], language="en")
    if not results:
        return text, False
    anonymized = anonymizer.anonymize(
        text=text,
        analyzer_results=results,
        operators={"PERSON": operator_config("replace", {"new_value": "[PERSON]"})},
    )
    return anonymized.text, True


def scrub_review_fields(
    title: str | None,
    text: str,
    enable_presidio: bool = False,
) -> tuple[str | None, str, list[str]]:
    """Scrub PII from both title (if provided) and text, merging PII types."""
    all_hits: list[str] = []

    clean_title: str | None = None
    if title:
        clean_title, title_hits = scrub_pii(title, enable_presidio=enable_presidio)
        for h in title_hits:
            if h not in all_hits:
                all_hits.append(h)

    clean_text, text_hits = scrub_pii(text, enable_presidio=enable_presidio)
    for h in text_hits:
        if h not in all_hits:
            all_hits.append(h)

    return clean_title, clean_text, all_hits
