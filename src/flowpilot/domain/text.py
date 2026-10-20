"""Unicode hygiene for untrusted text (lead content and model output).

Invisible characters can hide instructions from regex screens and from human reviewers while
remaining readable to a model: zero-width and other format characters, bidi overrides
("Trojan Source"), Unicode tag characters ("ASCII smuggling"), and stray control characters.
"""

from __future__ import annotations

import re
import unicodedata

# Format (Cf), surrogate (Cs), private-use (Co), and line/paragraph separator (Zl/Zp) characters.
_HIDDEN_CATEGORIES = frozenset({"Cf", "Cs", "Co", "Zl", "Zp"})
_ALLOWED_CONTROLS = frozenset("\n\r\t")

# Characters with essentially no legitimate use in a business enquiry and a known attack use:
# bidi embeddings/overrides/isolates and the Unicode tag block.
SMUGGLING = re.compile("[\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]")


def is_hidden(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category in _HIDDEN_CATEGORIES or (category == "Cc" and ch not in _ALLOWED_CONTROLS)


def has_hidden(text: str) -> bool:
    return any(is_hidden(ch) for ch in text)


def strip_hidden(text: str) -> str:
    """Remove invisible/control characters, keeping newlines, carriage returns, and tabs."""
    return "".join(ch for ch in text if not is_hidden(ch))


def normalize_for_screening(text: str) -> str:
    """Canonical form for pattern matching: NFKC folds fullwidth/compatibility forms to ASCII,
    then invisible characters that could split keywords are removed."""
    return strip_hidden(unicodedata.normalize("NFKC", text))
