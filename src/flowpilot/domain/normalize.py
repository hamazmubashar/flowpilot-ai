"""Normalization used for dedupe and for the synthetic-data-only guarantee."""

from __future__ import annotations

import re

# RFC 2606 / RFC 6761 reserved names. Accepting only these makes it structurally impossible to
# store a real person's email address.
RESERVED_TLDS = (".example", ".test", ".invalid")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")

LEGAL_SUFFIXES = frozenset(
    {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "gmbh", "plc"}
)

_EMAIL = re.compile(r"^[a-z0-9._%+-]+@(?P<domain>[a-z0-9-]+(?:\.[a-z0-9-]+)+)$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def email_domain(email: str) -> str | None:
    match = _EMAIL.match(email)
    return match["domain"] if match else None


def is_synthetic_domain(domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    if domain.endswith(RESERVED_TLDS):
        return True
    return any(domain == d or domain.endswith("." + d) for d in RESERVED_DOMAINS)


def company_key(name: str) -> str:
    """Case-, punctuation-, and legal-suffix-insensitive key: 'ACME, Inc.' -> 'acme'."""
    tokens = _NON_ALNUM.sub(" ", name.casefold()).split()
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)
