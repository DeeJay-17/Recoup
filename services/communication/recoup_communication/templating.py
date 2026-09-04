from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, meta
from jinja2.exceptions import UndefinedError
from recoup_common.errors import NotFoundError, ValidationError

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def list_templates() -> list[dict[str, Any]]:
    out = []
    for f in sorted(TEMPLATE_DIR.glob("*.j2")):
        src = f.read_text()
        desc = ""
        if src.startswith("{#"):
            desc = src[2 : src.index("#}")].strip()
        out.append(
            {
                "name": f.stem,
                "description": desc,
                "variables": sorted(meta.find_undeclared_variables(_env.parse(src))),
            }
        )
    return out


def required_variables(name: str) -> set[str]:
    path = TEMPLATE_DIR / f"{name}.j2"
    if not path.exists():
        raise NotFoundError(f"template '{name}' not found")
    return set(meta.find_undeclared_variables(_env.parse(path.read_text())))


def render(name: str, variables: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Returns (subject, body_text, missing_variables). Raises if a variable is missing."""
    missing = sorted(required_variables(name) - set(variables))
    if missing:
        raise ValidationError(
            f"template '{name}' is missing variables: {', '.join(missing)}",
            details={"missing_variables": missing},
        )
    try:
        text = _env.get_template(f"{name}.j2").render(**variables).strip()
    except UndefinedError as e:
        raise ValidationError(str(e)) from e
    first, _, rest = text.partition("\n")
    if not first.lower().startswith("subject:"):
        raise ValidationError(f"template '{name}' must start with a 'Subject:' line")
    return first.split(":", 1)[1].strip(), rest.strip() + "\n", []
