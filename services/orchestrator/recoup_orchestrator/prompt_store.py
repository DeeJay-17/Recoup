"""Versioned prompts: seeded from ``prompts/*.md``, editable via the API, resolved per run."""

from __future__ import annotations

from pathlib import Path

from recoup_common.db import utcnow
from recoup_common.errors import ConflictError, NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_orchestrator.models import PromptVersion

PROMPT_DIR = Path(__file__).parent / "prompts"


def file_prompts() -> dict[str, str]:
    return {p.stem: p.read_text().strip() for p in sorted(PROMPT_DIR.glob("*.md"))}


async def seed_from_files(session: AsyncSession) -> int:
    """Seed new prompts; re-seed changed repo prompts as a new version.

    A repo change becomes the active version only when the current active version was itself
    seeded from the repo, so prompts edited by humans in the console are never overridden.
    """
    n = 0
    rows = list((await session.scalars(select(PromptVersion))).all())
    by_name: dict[str, list[PromptVersion]] = {}
    for r in rows:
        by_name.setdefault(r.name, []).append(r)
    for name, content in file_prompts().items():
        versions = sorted(by_name.get(name, []), key=lambda v: v.version)
        if any(v.content == content for v in versions):
            continue
        active = next((v for v in versions if v.is_active), None)
        activate = active is None or active.created_by == "system:seed"
        v = PromptVersion(
            name=name,
            version=(versions[-1].version + 1) if versions else 1,
            content=content,
            notes="seeded from repo" if not versions else "re-seeded: repo prompt changed",
            created_by="system:seed",
            created_at=utcnow(),
            is_active=activate,
        )
        if activate:
            for old in versions:
                old.is_active = False
        session.add(v)
        n += 1
    return n


async def active_bundle(session: AsyncSession) -> dict[str, PromptVersion]:
    rows = await session.scalars(select(PromptVersion).where(PromptVersion.is_active.is_(True)))
    return {r.name: r for r in rows.all()}


async def list_prompts(session: AsyncSession) -> list[PromptVersion]:
    rows = await session.scalars(
        select(PromptVersion).order_by(PromptVersion.name, PromptVersion.version.desc())
    )
    return list(rows.all())


async def add_version(
    session: AsyncSession,
    name: str,
    content: str,
    *,
    notes: str | None,
    created_by: str,
    activate: bool,
) -> PromptVersion:
    if name not in file_prompts() and not await session.scalar(
        select(PromptVersion).where(PromptVersion.name == name)
    ):
        raise NotFoundError(f"unknown prompt '{name}'")
    latest = await session.scalar(
        select(PromptVersion)
        .where(PromptVersion.name == name)
        .order_by(PromptVersion.version.desc())
    )
    v = PromptVersion(
        name=name,
        version=(latest.version + 1) if latest else 1,
        content=content,
        notes=notes,
        created_by=created_by,
        created_at=utcnow(),
        is_active=False,
    )
    session.add(v)
    await session.flush()
    if activate:
        await activate_version(session, name, v.version)
    return v


async def activate_version(session: AsyncSession, name: str, version: int) -> PromptVersion:
    rows = list(
        (await session.scalars(select(PromptVersion).where(PromptVersion.name == name))).all()
    )
    target = next((r for r in rows if r.version == version), None)
    if target is None:
        raise NotFoundError(f"prompt {name} v{version} not found")
    if target.is_active:
        raise ConflictError(f"prompt {name} v{version} is already active")
    for r in rows:
        r.is_active = r.version == version
    return target
