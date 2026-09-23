"""Label, status, and number parsing shared by the BPS adapters."""

from __future__ import annotations

import hashlib
import re
from html import unescape

from tilik_agent.ingestion.contracts import ObservationDraft

MAPPING_VERSION = "1"
CONVERSION_VERSION = "1"
UNSPECIFIED_UNIT = "unspecified"

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_AGE_BAND_RE = re.compile(r"^\d{1,3}\s*[-–—]\s*\d{1,3}$")

STATIC_STATUS = {"-", "–", "—", "na", "n.a", "n.a.", "n/a", "...", "*", "**", "***"}
SIMDASI_STATUS = STATIC_STATUS | {"e", "r", "~0", "a", "b", "c", "unavailable"}

_CONVERSIONS: dict[str, tuple[str, float]] = {
    "orang": ("person", 1.0),
    "jiwa": ("person", 1.0),
    "ribu jiwa": ("person", 1000.0),
    "persen": ("ratio", 0.01),
    "%": ("ratio", 0.01),
}

SEED_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("Jenis Kelamin", "dimension:sex"),
    ("Laki-laki", "member:male"),
    ("Laki-Laki", "member:male"),
    ("Perempuan", "member:female"),
    ("Provinsi", "dimension:geography"),
    ("38 Provinsi", "dimension:geography"),
    ("Perkotaan", "member:urban"),
    ("Perdesaan", "member:rural"),
    ("Perkotaan+Perdesaan", "member:urban_rural_combined"),
    ("Indonesia", "member:national"),
    ("INDONESIA", "member:national"),
    ("Jumlah", "member:total"),
)


def normalize_label(value: str) -> str:
    """Collapse case, tags, and whitespace so labels can be compared."""
    text = unescape(value or "")
    text = _TAG_RE.sub("", text)
    text = text.replace("\xa0", " ")
    text = _SPACE_RE.sub(" ", text).strip().casefold()
    return text


def stable_code(value: str) -> str:
    """Return a source code that fits in a 255-character column."""
    text = normalize_label(value) or "blank"
    if len(text) <= 200:
        return text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_dimension_type(label: str) -> str:
    """Map a dimension label onto a known role. Unknown labels stay generic."""
    text = normalize_label(label)
    if "jenis kelamin" in text:
        return "sex"
    if text.startswith("provinsi") or "kabupaten" in text or text == "38 provinsi":
        return "geography"
    if "kelompok umur" in text:
        return "age_group"
    if text in {"klasifikasi daerah", "daerah tempat tinggal", "tipe daerah"}:
        return "urban_rural"
    if text in {"tahun", "turunan tahun"}:
        return "time_subperiod"
    return "generic_category"


def canonical_member(label: str) -> str | None:
    """Return a canonical member code when the label is in the explicit registry."""
    spaced = normalize_label(label)
    compact = spaced.replace(" ", "")
    if compact in {"perkotaan+perdesaan"}:
        return "urban_rural_combined"
    if "+" in spaced:
        return "total"
    if spaced in {"laki-laki", "laki laki"}:
        return "male"
    if spaced == "perempuan":
        return "female"
    if spaced in {"jumlah", "total"} or spaced.startswith("jumlah "):
        return "total"
    if spaced == "indonesia":
        return "national"
    if compact == "perkotaan":
        return "urban"
    if compact == "perdesaan":
        return "rural"
    return None


def is_status_token(value: str, *, letter_flags: bool) -> bool:
    """Return whether the whole cell is a BPS status marker."""
    token = value.strip()
    folded = token.casefold()
    allowed = SIMDASI_STATUS if letter_flags else STATIC_STATUS
    return token in allowed or folded in allowed


def parse_indonesian_number(text: str, *, decimal_places: int | None = None) -> float | None:
    """Parse a SIMDASI number. Dots group thousands and a comma is the decimal mark.

    Returns None when the text is not a number. Status tokens are not numbers.
    """
    token = text.strip().replace(" ", "")
    if not token or is_status_token(token, letter_flags=True):
        return None
    if "," in token:
        token = token.replace(".", "").replace(",", ".")
    elif _THOUSANDS_RE.fullmatch(token) or decimal_places == 0:
        token = token.replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


def convert_value(numeric: float | None, unit: str | None, status: str | None) -> tuple[float | None, str | None]:
    """Scale a value only when the unit conversion is certain and the cell is numeric."""
    if numeric is None or status is not None or not unit:
        return None, None
    rule = _CONVERSIONS.get(normalize_label(unit))
    if rule is None:
        return None, None
    canonical_unit, scale = rule
    return numeric * scale, canonical_unit


def infer_unit(label: str, explicit: str | None = None) -> str:
    """Choose the source unit from metadata, otherwise from an explicit percent label."""
    if explicit and normalize_label(explicit) not in {"", "null", "none"}:
        return _TAG_RE.sub("", unescape(explicit)).strip() or UNSPECIFIED_UNIT
    folded = normalize_label(label)
    if "(%)" in label or "persen" in folded or folded.startswith("persentase"):
        return "Persen"
    return UNSPECIFIED_UNIT


def year_in_text(value: str) -> list[int]:
    """Return standalone 4-digit years. Digits inside ``SP2020`` are not years."""
    return [int(match) for match in re.findall(r"\b(?:19|20)\d{2}\b", value)]


def is_year_token(text: str, number: float | None) -> int | None:
    """Return a year when the cell is exactly a year, not a measurement that happens to be large."""
    if number is not None and float(number).is_integer():
        year = int(number)
        if 1900 <= year <= 2100:
            return year
    stripped = text.strip().replace(".0", "")
    if _YEAR_RE.fullmatch(stripped):
        return int(stripped)
    return None


def is_age_band(text: str) -> bool:
    """Return whether a header looks like an age interval such as ``15-19``."""
    return _AGE_BAND_RE.fullmatch(text.strip()) is not None


def format_excel_number(number: float) -> str:
    """Render an Excel float without a trailing ``.0`` on integers."""
    if float(number).is_integer() and abs(number) < 1e15:
        return str(int(number))
    return format(number, ".12g")


def observation_fingerprint(draft: ObservationDraft) -> str:
    """Hash the fields that make a re-load a no-op when the source cell is unchanged."""
    dimension_text = "|".join(f"{item.source_code}={item.member_source_code}" for item in draft.dimensions)
    payload = "\n".join(
        [
            draft.measure_source_code,
            draft.period_source_code,
            draft.period_label,
            draft.source_cell_key,
            draft.source_value,
            "" if draft.numeric_value is None else repr(draft.numeric_value),
            draft.status_code or "",
            draft.measure_unit,
            dimension_text,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def duplicate_reason(observations: list[ObservationDraft]) -> str | None:
    """Reject a second mapping of the same source cell or dimension coordinate, or a cell on one axis twice."""
    cells: set[str] = set()
    coordinates: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    for draft in observations:
        if draft.source_cell_key in cells:
            return f"Duplicate source cell {draft.source_cell_key}"
        cells.add(draft.source_cell_key)
        axes = [item.source_code for item in draft.dimensions]
        if len(axes) != len(set(axes)):
            repeated = next(axis for axis in axes if axes.count(axis) > 1)
            return f"Source cell {draft.source_cell_key} has two members on dimension {repeated}"
        coordinate = (
            draft.measure_source_code,
            draft.period_label,
            tuple((item.source_code, item.member_source_code) for item in draft.dimensions),
        )
        if coordinate in coordinates:
            return "Duplicate dimension combination for a measure and period"
        coordinates.add(coordinate)
    return None


def published_years(oldest: int | None, latest: int | None) -> list[int | None]:
    """List years to request. A missing bound requests the latest year, or one undated call."""
    if isinstance(oldest, int) and isinstance(latest, int) and oldest <= latest:
        return list(range(oldest, latest + 1))
    if isinstance(latest, int):
        return [latest]
    return [None]
