"""Conservative province identity for statistical joins."""

from __future__ import annotations

import re

_ALIASES = {
    "kep riau": "2100000",
    "kep bangka belitung": "1900000",
    "bangka belitung": "1900000",
    "dki jakarta": "3100000",
    "d k i jakarta": "3100000",
    "di yogyakarta": "3400000",
    "d i yogyakarta": "3400000",
    "d i y": "3400000",
    "diy": "3400000",
    "34 d i y": "3400000",
}


def normalize_province(label: str) -> str:
    text = re.sub(r"(?<=[A-Za-z])\s*\d+\)?\s*$", "", label.strip())
    text = re.sub(r"^\d{2}\s+", "", text)
    text = re.sub(r"[^\w]+", " ", text.casefold())
    parts = text.split()
    if len(parts) > 2 and all(len(part) == 1 for part in parts):
        return "".join(parts)
    return " ".join(parts)


def province_code(label: str, known_names: dict[str, str]) -> str | None:
    key = normalize_province(label)
    if key in {"indonesia", "jumlah", "total", "catatan"} or key.isdigit():
        return None
    return _ALIASES.get(key) or known_names.get(key)


def is_nonprovince(label: str) -> bool:
    key = normalize_province(label)
    return key in {"indonesia", "jumlah", "total", "catatan"} or key.isdigit()


def province_names(rows: list[tuple[str, str]]) -> dict[str, str]:
    """Use the geography registry; ambiguous normalized names stay unresolved."""
    names: dict[str, str] = {}
    ambiguous: set[str] = set()
    for code, label in rows:
        if code == "0000000":
            continue
        key = normalize_province(label)
        if key in names and names[key] != code:
            ambiguous.add(key)
        else:
            names[key] = code
    return {key: code for key, code in names.items() if key not in ambiguous}
