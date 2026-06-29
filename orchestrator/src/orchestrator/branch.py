import re

_MAX_TAGLINE = 40


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > _MAX_TAGLINE:
        s = s[:_MAX_TAGLINE].rstrip("-")
    return s


def derive_tagline(task_text: str, fallback: str) -> str:
    for line in task_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            slug = _slug(stripped[2:])
            if slug:
                return slug
    return _slug(fallback)


def branch_name(task_id: int, tagline: str) -> str:
    return f"feat/{task_id}-{tagline}"
