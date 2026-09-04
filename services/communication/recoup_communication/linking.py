"""Pure helpers: pull invoice numbers out of text, normalise addresses, build Message-IDs."""

from __future__ import annotations

import re
import uuid

INVOICE_RE = re.compile(r"\bINV-\d{5,8}\b", re.IGNORECASE)
_ADDR_RE = re.compile(r"<([^>]+)>")


def extract_invoice_refs(*texts: str | None) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        if not t:
            continue
        for m in INVOICE_RE.findall(t):
            seen[m.upper()] = None
    return list(seen)


def normalise_address(raw: str) -> str:
    """'Jane Doe <jane@x.com>' -> 'jane@x.com' (lower-cased)."""
    m = _ADDR_RE.search(raw)
    return (m.group(1) if m else raw).strip().lower()


def new_message_id(domain: str) -> str:
    return f"<{uuid.uuid4().hex}@{domain}>"


def strip_reply_prefix(subject: str) -> str:
    s = subject.strip()
    while True:
        m = re.match(r"^(re|fw|fwd)\s*:\s*", s, re.IGNORECASE)
        if not m:
            return s
        s = s[m.end() :]
