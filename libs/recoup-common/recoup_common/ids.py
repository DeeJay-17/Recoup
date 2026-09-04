from __future__ import annotations

import uuid


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def new_id_str() -> str:
    return str(uuid.uuid4())
