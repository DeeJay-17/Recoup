"""Light PII redaction applied to tool results before they enter model context.

Business emails are kept (agents need them to reach AP contacts); phone numbers, card-like and
SSN-like digit runs are masked. Presidio can be dropped in behind ``redact_text`` later.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card]"),
    (
        re.compile(
            r"(?<![\w.])(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}"
            r"(?:\s*(?:x|ext\.?)\s*\d{1,5})?\b"
        ),
        "[phone]",
    ),
]


def redact_text(s: str) -> str:
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    return obj


def truncate(obj: Any, max_chars: int) -> Any:
    """Keep large payloads out of the model context; the full result stays in the audit row."""
    import json

    s = json.dumps(obj, default=str)
    if len(s) <= max_chars:
        return obj
    return {"_truncated": True, "_chars": len(s), "preview": s[:max_chars]}
