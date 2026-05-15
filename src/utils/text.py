from __future__ import annotations

import re
from typing import Any


REPEATED_PREFIXES = (
    "13F:",
    "테마:",
    "사업:",
    "미래산업테마:",
)


def clean_phrase(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value).strip()
    changed = True
    while changed:
        changed = False
        for prefix in REPEATED_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "N/A", "n/a", "nan", "None"}:
        return None
    text = text.replace(",", "").replace("%", "")
    text = re.sub(r"[^\d.+-]", "", text)
    if text in {"", ".", "+", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def compact_join(parts: list[Any], sep: str = " / ") -> str | None:
    clean = [clean_phrase(part) for part in parts if clean_phrase(part) not in (None, "")]
    if not clean:
        return None
    return sep.join(str(part) for part in clean)

