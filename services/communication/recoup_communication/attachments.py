from __future__ import annotations

import io


def extract_text(content: bytes, content_type: str, *, max_chars: int) -> str | None:
    """Best-effort text extraction for remittance advices etc. PDF via pdfplumber, text as-is."""
    ct = content_type.lower()
    if ct.startswith("text/"):
        return content.decode("utf-8", errors="replace")[:max_chars]
    if ct == "application/pdf":
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                parts = [p.extract_text() or "" for p in pdf.pages[:10]]
            return "\n".join(parts)[:max_chars]
        except Exception as e:
            return f"[pdf extraction failed: {e}]"
    return None
