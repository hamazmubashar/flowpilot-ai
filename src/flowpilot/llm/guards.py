"""Deterministic guards around the model: an input pre-screen and output content checks.

The pre-screen is a cheap first layer, not a complete defence. The real protection is that model
output has no authority: it can only create advisory rows that a human reviews.
"""

from __future__ import annotations

import re

_I = re.IGNORECASE
_IM = re.IGNORECASE | re.MULTILINE

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\bignore\b.{0,40}\b(previous|prior|above|earlier|all|any|your)\b.{0,20}"
        r"\b(instructions?|prompts?|rules?|messages?|directions?)\b",
        _I,
    ),
    "override_instructions": re.compile(
        r"\b(disregard|forget|override|bypass)\b.{0,40}"
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
}

SUMMARY_CHECKS = ("url", "email", "phone", "role_marker")


def detect_injection(*texts: str) -> list[str]:
    """Names of injection patterns found in any of the given untrusted texts."""
    joined = "\n".join(texts)
    return [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(joined)]


def _violations(text: str, checks: tuple[str, ...]) -> list[str]:
    found = [name for name in checks if OUTPUT_PATTERNS[name].search(text)]
    if detect_injection(text):
        found.append("injection_echo")
    return found


def check_draft(subject: str, body: str) -> list[str]:
    return _violations(f"{subject}\n{body}", tuple(OUTPUT_PATTERNS))


def check_summary(summary: str) -> list[str]:
    return _violations(summary, SUMMARY_CHECKS)
