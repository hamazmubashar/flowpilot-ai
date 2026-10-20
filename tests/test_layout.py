"""Whitespace-only draft layout repair (llm/layout.py). Offline, deterministic."""

import pytest

from flowpilot.llm.guards import check_draft
from flowpilot.llm.layout import normalize_draft_layout
from tests.test_llm_guards import CLEAN_BODY

# Verbatim shape returned by qwen2.5:3b on prompt p2.
LIVE_P2 = (
    "Hi Rowan, I hope this message finds you well. We've reviewed your message and appreciate "
    "your interest in process automation for your dispatch team. We'd like to schedule a brief "
    "discovery call to discuss how we can help streamline your operations. Please let us know a "
    "convenient time for you, and we'll arrange it. Best regards, Northstar Digital Solutions"
)
LIVE_P2_REPAIRED = (
    "Hi Rowan,\n\nI hope this message finds you well. We've reviewed your message and appreciate "
    "your interest in process automation for your dispatch team. We'd like to schedule a brief "
    "discovery call to discuss how we can help streamline your operations. Please let us know a "
    "convenient time for you, and we'll arrange it.\n\nBest regards,\nNorthstar Digital Solutions"
)


def test_single_line_live_draft_is_repaired_exactly() -> None:
    assert check_draft("Re: hi", LIVE_P2) != []  # rejected as returned by the model
    assert normalize_draft_layout(LIVE_P2) == LIVE_P2_REPAIRED
    assert check_draft("Re: your enquiry", LIVE_P2_REPAIRED) == []


@pytest.mark.parametrize(
    "body",
    [
        CLEAN_BODY,
        LIVE_P2_REPAIRED,
        "Hello Sage,\n\nThanks.\n\nMore.\n\nBest regards,\nNorthstar Digital Solutions",
    ],
)
def test_compliant_drafts_are_unchanged(body: str) -> None:
    assert normalize_draft_layout(body) == body
    assert normalize_draft_layout(normalize_draft_layout(body)) == body  # idempotent


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (  # greeting followed by a single newline
            "Hi Rowan,\nThanks for writing.\n\nBest regards,\nNorthstar Digital Solutions",
            "Hi Rowan,\n\nThanks for writing.\n\nBest regards,\nNorthstar Digital Solutions",
        ),
        (  # closing glued to the paragraph by one newline
            "Hi Rowan,\n\nThanks for writing.\nBest regards,\nNorthstar Digital Solutions",
            "Hi Rowan,\n\nThanks for writing.\n\nBest regards,\nNorthstar Digital Solutions",
        ),
        (  # closing and sign-off on one line with extra spaces
            "Hi Rowan,\n\nThanks for writing.   Best regards,   Northstar Digital Solutions  ",
            "Hi Rowan,\n\nThanks for writing.\n\nBest regards,\nNorthstar Digital Solutions",
        ),
        (  # CRLF endings and trailing spaces
            "Dear O'Neil,\r\n\r\nThanks for writing.  \r\n\r\n"
            "Best regards,\r\nNorthstar Digital Solutions",
            "Dear O'Neil,\n\nThanks for writing.\n\nBest regards,\nNorthstar Digital Solutions",
        ),
    ],
)
def test_layout_rules(body: str, expected: str) -> None:
    assert normalize_draft_layout(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        LIVE_P2,
        CLEAN_BODY,
        "Hi Rowan, a. b. c. Best regards, Northstar Digital Solutions",
        "No greeting here. Best regards, Northstar Digital Solutions",
        "Hi Rowan, thanks. Kind regards, Northstar Digital Solutions",
    ],
)
def test_only_whitespace_changes(body: str) -> None:
    assert normalize_draft_layout(body).split() == body.split()


@pytest.mark.parametrize(
    ("body", "violation"),
    [
        # Malformed sign-offs are never repaired.
        (
            "Hi Rowan, thanks for writing. Best regards, Northstar Digital Solutions.",
            "missing_signoff",
        ),
        ("Hi Rowan, thanks for writing. Best regards, The Northstar Team", "missing_signoff"),
        (
            "Hi Rowan, thanks for writing. Best regards, northstar digital solutions",
            "missing_signoff",
        ),
        ("Hi Rowan, thanks for writing. Best regards,", "missing_signoff"),
        # Wrong closing is never rewritten.
        (
            "Hi Rowan, thanks for writing. Kind regards, Northstar Digital Solutions",
            "missing_closing",
        ),
        # A missing greeting is never invented.
        ("Thanks for writing. Best regards, Northstar Digital Solutions", "missing_greeting_line"),
        # Lowercase greeting keyword is not a recognised greeting.
        (
            "hi Rowan, thanks for writing. Best regards, Northstar Digital Solutions",
            "missing_greeting_line",
        ),
    ],
)
def test_malformed_drafts_still_rejected_after_repair(body: str, violation: str) -> None:
    assert violation in check_draft("Re: your enquiry", normalize_draft_layout(body))


def test_unicode_separators_are_not_treated_as_layout() -> None:
    """U+2028 before the sign-off is left in place so the hidden-character guard rejects it."""
    body = "Hi Rowan,\n\nThanks for writing.\n\nBest regards,\u2028Northstar Digital Solutions"
    out = normalize_draft_layout(body)
    assert "\u2028" in out
    assert "hidden_characters" in check_draft("Re: your enquiry", out)


def test_non_breaking_space_greeting_not_repaired() -> None:
    body = "Hi\u00a0Rowan, thanks. Best regards, Northstar Digital Solutions"
    out = normalize_draft_layout(body)
    assert out.startswith("Hi\u00a0Rowan, thanks.")
    assert "missing_greeting_line" in check_draft("Re: your enquiry", out)
