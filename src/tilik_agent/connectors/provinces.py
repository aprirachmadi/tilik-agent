"""Exact province-name crosswalk to BPS codes for sources that use other numbering."""

from __future__ import annotations

import re

# Two-digit BPS codes as stored in Tilik's `geography` table (e.g. 9400000 -> "94").
# Bapanas and SiPongi number the Papua provinces the Kemendagri way (Papua = 91),
# so their own codes are never used as BPS codes.
BPS_PROVINCES = {
    "aceh": "11", "sumatera utara": "12", "sumatera barat": "13", "riau": "14", "jambi": "15",
    "sumatera selatan": "16", "bengkulu": "17", "lampung": "18", "kepulauan bangka belitung": "19",
    "kepulauan riau": "21", "dki jakarta": "31", "jawa barat": "32", "jawa tengah": "33",
    "di yogyakarta": "34", "jawa timur": "35", "banten": "36", "bali": "51",
    "nusa tenggara barat": "52", "nusa tenggara timur": "53", "kalimantan barat": "61",
    "kalimantan tengah": "62", "kalimantan selatan": "63", "kalimantan timur": "64",
    "kalimantan utara": "65", "sulawesi utara": "71", "sulawesi tengah": "72", "sulawesi selatan": "73",
    "sulawesi tenggara": "74", "gorontalo": "75", "sulawesi barat": "76", "maluku": "81",
    "maluku utara": "82", "papua barat": "91", "papua barat daya": "92", "papua": "94",
    "papua selatan": "95", "papua tengah": "96", "papua pegunungan": "97",
}
_ALIASES = {
    "d.i yogyakarta": "di yogyakarta", "daerah istimewa yogyakarta": "di yogyakarta",
    "yogyakarta": "di yogyakarta", "bangka belitung": "kepulauan bangka belitung",
}


def _key(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip().casefold())
    return _ALIASES.get(key, key)


def bps_province_code(name: str) -> str | None:
    """Return the BPS code for an exact province name, or None (e.g. IKN)."""
    return BPS_PROVINCES.get(_key(name))


def same_province(left: str, right: str) -> bool:
    return _key(left) == _key(right)
