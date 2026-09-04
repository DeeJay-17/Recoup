from __future__ import annotations

import re


def chunk_text(text: str, *, size: int = 1200, overlap: int = 150) -> list[str]:
    """Paragraph-aware sliding window. Keeps chunks roughly ``size`` chars with ``overlap``."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
            continue
        if cur:
            chunks.append(cur)
            cur = cur[-overlap:] if overlap and len(cur) > overlap else ""
        while len(p) > size:
            chunks.append((cur + "\n\n" + p[:size]).strip() if cur else p[:size])
            cur = ""
            p = p[size - overlap :]
        cur = f"{cur}\n\n{p}".strip() if cur else p
    if cur:
        chunks.append(cur)
    return chunks
