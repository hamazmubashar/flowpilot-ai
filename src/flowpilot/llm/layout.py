"""Deterministic, whitespace-only layout repair for model-generated drafts.

Small models often return a draft on a single line ("Hi Rowan, ... Best regards, Northstar
Digital Solutions"). This module re-inserts line breaks at three well-defined places. It never
adds, removes, reorders, or edits words or punctuation, so it cannot invent claims or drop
content; an invariant check enforces that. It does not repair a wrong or missing sign-off, and
it is not a security control: every content guard still runs on the original model text too.
"""

from __future__ import annotations

import re

from flowpilot.domain.fallbacks import CLOSING, SIGNOFF

# Only ASCII spaces/tabs/newlines are treated as layout. Unicode separators (U+2028, NBSP, ...) are
# left untouched so the hidden-character guard still sees and rejects them.
_WS = "[ \t\n]"

# Rule 2: a greeting ("Hi/Hello/Dear <Name>,") followed on the same line, or after a single line
# break, by more text.
_GREETING = re.compile(
    r"\A(?P<greeting>(?:Hi|Hello|Dear)[ \t]+[A-Za-z][A-Za-z'-]{0,39},)"
    r"(?:[ \t]+|[ \t]*\n[ \t]*)(?=[^ \t\n])"
)

# Rule 3: the exact closing and the exact sign-off at the very end, separated by any ASCII
# whitespace instead of their own lines.
_CLOSING_BLOCK = re.compile(rf"{_WS}+{re.escape(CLOSING)}{_WS}+{re.escape(SIGNOFF)}{_WS}*\Z")


def _visible_words(text: str) -> list[str]:
    return text.split()


def normalize_draft_layout(body: str) -> str:
    """Return `body` with repaired line breaks, or unchanged if no rule applies.

    Rules, applied in order:
    1. Normalize line endings (CRLF/CR -> LF), strip trailing spaces/tabs on each line, and
       trim leading/trailing ASCII whitespace.
    2. Put a leading "Hi|Hello|Dear <Name>," greeting on its own line, followed by a blank line.
    3. If the text ends with exactly "Best regards," + whitespace + "Northstar Digital Solutions",
       move them to their own lines, preceded by a blank line.
    """
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n")).strip(" \t\n")
    text = _GREETING.sub(lambda m: f"{m['greeting']}\n\n", text, count=1)
    text = _CLOSING_BLOCK.sub(f"\n\n{CLOSING}\n{SIGNOFF}", text, count=1)

    # Invariant: only layout changed. Same visible words, same order, same characters.
    if _visible_words(text) != _visible_words(body):
        return body
    return text
