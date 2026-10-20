"""Deterministic guards around the model: an input pre-screen and output content checks.

The pre-screen is a cheap first layer, not a complete defence. The real protection is that model
output has no authority: it can only create advisory rows that a human reviews.
"""

from __future__ import annotations

import re

from flowpilot.domain.fallbacks import CLOSING, SIGNOFF
from flowpilot.domain.text import SMUGGLING, has_hidden, normalize_for_screening

_I = re.IGNORECASE
_IM = re.IGNORECASE | re.MULTILINE

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    # Bridges use [\s\S] (not ".") so a line break between the trigger words cannot evade them.
    "ignore_instructions": re.compile(
        r"\bignore\b[\s\S]{0,40}\b(previous|prior|above|earlier|all|any|your)\b[\s\S]{0,20}"
        r"\b(instructions?|prompts?|rules?|messages?|directions?)\b",
        _I,
    ),
    "override_instructions": re.compile(
        r"\b(disregard|forget|override|bypass)\b[\s\S]{0,40}"
        r"\b(instructions?|prompts?|rules?|guidelines?|guardrails?)\b",
        _I,
    ),
    "system_prompt": re.compile(r"\bsystem\s+(prompt|message|instructions?)\b", _I),
    "role_change": re.compile(
        r"\byou\s+are\s+now\b|\b(act|behave|respond)\s+as\s+(an?\s+)?"
        r"(different|new|unrestricted|unfiltered|admin|administrator|developer|system)\b",
        _I,
    ),
    "role_tokens": re.compile(
        r"<\|?\s*(im_start|im_end|system|assistant|user|endoftext|start_of_turn|end_of_turn)"
        r"\s*\|?>|\[/?(INST|SYS)\]",
        _I,
    ),
    # Role prefix at a line or sentence start ("Hi. system: ..."), not "our ERP system: ...".
    "role_prefix": re.compile(r"(^|[.!?]\s+)\s*(system|assistant|developer)\s*:", _IM),
    "fake_block": re.compile(r"\bBEGIN\s+(SYSTEM|INSTRUCTIONS?|PROMPT|ADMIN)\b", _I),
    "jailbreak_mode": re.compile(r"\b(developer|god|dan|jailbreak|admin)\s+mode\b", _I),
    "approval_request": re.compile(
        r"\b(auto[- ]?approve|approve\s+(this|the|my)\s+(lead|draft|message|request))\b", _I
    ),
}

OUTPUT_PATTERNS: dict[str, re.Pattern[str]] = {
    "url": re.compile(
        r"(https?://|www\.|\b[a-z0-9-]+\.(com|net|org|io|ai|co|dev|app|example)\b)", _I
    ),
    "email": re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),
    "phone": re.compile(r"\+?\d[\d\s().-]{7,}\d"),
    "currency": re.compile(
        r"[$€£¥]\s?\d|\b\d[\d,.]*\s?(k|m|usd|eur|gbp|dollars?|euros?|pounds?)\b", _I
    ),
    "placeholder": re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[^>\n]{1,40}>"),
    "role_marker": re.compile(r"<\|[a-z_]+\|>|^\s*(system|assistant|user)\s*:", _IM),
    # The prospect's budget must never be referenced, directly or by obvious paraphrase.
    "budget": re.compile(
        r"\bbudget\w*"
        r"|\b(price|pricing|spend|spending|investment|funding|cost|financial)\s+"
        r"(range|bracket|band|envelope|level|ceiling|cap|limit|parameters|constraints)s?\b"
        r"|\b(what|how\s+much)\s+(you|your\s+(team|company|organi[sz]ation))\s+"
        r"(can|could|are\s+able\s+to|plan\s+to|intend\s+to|want\s+to|would\s+like\s+to)\s+"
        r"(spend|invest|allocate|afford)\b"
        r"|\bwithin\s+your\s+means\b|\bearmarked\b"
        r"|\b(under|over)_\d+k\b|\b\d+k_\d+k\b|\bundisclosed\b",
        _I,
    ),
}

SUMMARY_CHECKS = ("url", "email", "phone", "role_marker")


def detect_injection(*texts: str) -> list[str]:
    """Names of injection patterns found in any of the given untrusted texts.

    Matching runs on NFKC-normalized text with invisible characters removed, so zero-width
    splitting ("ig\u200bnore") and fullwidth forms cannot evade the patterns. Bidi controls and
    Unicode tag characters are themselves treated as an injection signal.
    """
    raw = "\n".join(texts)
    joined = normalize_for_screening(raw)
    found = [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(joined)]
    if SMUGGLING.search(raw):
        found.append("hidden_characters")
    return found


def _violations(text: str, checks: tuple[str, ...]) -> list[str]:
    screened = normalize_for_screening(text)
    found = [name for name in checks if OUTPUT_PATTERNS[name].search(screened)]
    if has_hidden(text):
        found.append("hidden_characters")
    if detect_injection(text):
        found.append("injection_echo")
    return found


# Violations that depend only on line layout. Whitespace-only normalization may legitimately clear
# these; every other violation is judged on the original model text as well.
FORMAT_VIOLATIONS = frozenset(
    {
        "missing_paragraph_breaks",
        "missing_greeting_line",
        "missing_closing",
        "paragraph_too_long",
        "missing_signoff",
    }
)

GREETING_MAX_CHARS = 60
PARAGRAPH_MAX_CHARS = 600
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def format_violations(body: str) -> list[str]:
    """Required layout: a greeting line on its own, at least one body paragraph, blank lines
    between paragraphs, paragraphs of readable length, then "Best regards," and the sign-off."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = [block.strip() for block in _BLANK_LINE.split(text) if block.strip()]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found = []
    if len(blocks) < 3:
        found.append("missing_paragraph_breaks")
    greeting = lines[0] if lines else ""
    if not (
        blocks and blocks[0] == greeting and len(greeting) <= GREETING_MAX_CHARS
    ) or not greeting.endswith(","):
        found.append("missing_greeting_line")
    if len(lines) < 2 or lines[-2] != CLOSING:
        found.append("missing_closing")
    if any(len(block) > PARAGRAPH_MAX_CHARS for block in blocks):
        found.append("paragraph_too_long")
    return found


def has_signoff(body: str) -> bool:
    """The last non-empty line must be exactly the company sign-off."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == SIGNOFF


def check_draft(subject: str, body: str) -> list[str]:
    found = _violations(f"{subject}\n{body}", tuple(OUTPUT_PATTERNS))
    if "\n" in subject or "\r" in subject or has_hidden(subject):
        found.append("invalid_subject")  # header-injection risk once messages are sent
    if not has_signoff(body):
        found.append("missing_signoff")
    found.extend(format_violations(body))
    return found


def check_summary(summary: str) -> list[str]:
    return _violations(summary, SUMMARY_CHECKS)
