"""Extractors for the static workbooks the generic layout parser cannot read.

Each extractor is written against one BPS table and checks the landmarks it relies on, so a republished
workbook with a different shape goes to review instead of loading misplaced numbers.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path

from tilik_agent.ingestion.adapters.static import Cell, dimension_draft, load_sheets, value_draft
from tilik_agent.ingestion.contracts import ObservationDraft, ParseResult
from tilik_agent.ingestion.parsing import duplicate_reason

Grid = list[list[Cell]]
Extraction = tuple[list[ObservationDraft], str]
Extractor = Callable[[Path], Extraction]

NO_DATA: dict[str, str] = {
    "MjExMSMx": "Link to the SDKI 2017 report on the BKKBN site; the workbook holds no figures",
    "MjAwOCMx": "Link to the Kemendikbud education statistics summary; the workbook holds no figures",
    "MTk3MSMx": "Link to the Puspendik national exam report; the workbook holds no figures",
    "MTk3MCMx": "Link to the Ditjen PAS prison population portal; the workbook holds no figures",
    "MTk3MiMx": "Link to the Puspendik exam integrity index; the workbook holds no figures",
    "MTk2NyMx": "Link to the Kemenkeu education budget pages; the workbook holds no figures",
}
"""Tables whose workbook only points to an outside publication.

Four of them also carry a ``Perkembangan Upah Buruh`` sheet copied from an unrelated table, so the
generic parser must not read them either.
"""

_RSE_FLAG = "rse 25-50%"
_MONTH_PERIOD = re.compile(r"((?:19|20)\d{2})\s+([A-Za-z]+)")
_FOOTNOTE_SUFFIX = re.compile(r"\s+\d+\)?$")


class LayoutChanged(Exception):
    """The workbook no longer has the landmarks its extractor was written against."""


def parse_manual(table_id: str, path: Path) -> ParseResult:
    """Run the extractor registered for ``table_id`` and apply the same checks as the generic parser."""
    try:
        observations, notes = EXTRACTORS[table_id](path)
    except LayoutChanged as exc:
        return ParseResult(needs_review=True, review_reason=f"Manual extractor for {table_id}: {exc}")
    duplicate = duplicate_reason(observations)
    if duplicate:
        return ParseResult(needs_review=True, review_reason=duplicate, notes=notes)
    if not observations:
        return ParseResult(needs_review=True, review_reason=f"Manual extractor for {table_id} found no values")
    return ParseResult(observations=observations, notes=notes)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise LayoutChanged(message)


def _sheet(path: Path, name: str | None = None) -> tuple[str, Grid]:
    sheets = load_sheets(path)
    for sheet_name, grid in sheets:
        if name is None or sheet_name == name:
            return sheet_name, grid
    raise LayoutChanged(f"sheet {name!r} is missing")


def _cell(grid: Grid, row: int, column: int) -> Cell:
    if row < len(grid) and column < len(grid[row]):
        return grid[row][column]
    return Cell("", None)


def _text(grid: Grid, row: int, column: int) -> str:
    return " ".join(_cell(grid, row, column).text.split())


def _width(grid: Grid) -> int:
    return max((len(row) for row in grid), default=0)


def _draft(
    sheet: str,
    row: int,
    column: int,
    cell: Cell,
    *,
    measure: str,
    period: str,
    dimensions: list[tuple[str, str]],
) -> ObservationDraft:
    return value_draft(
        sheet_name=sheet,
        row_index=row,
        column=column,
        measure_label=measure,
        period=period,
        cell=cell,
        dimensions=tuple(dimension_draft(axis, member) for axis, member in dimensions),
        unit_label=measure,
    )


def _strip_footnote(label: str) -> str:
    """``DI Yogyakarta 2`` and ``Leding 1`` carry a footnote number after the label."""
    return _FOOTNOTE_SUFFIX.sub("", label).strip()


def _month_period(label: str) -> str:
    """``2005 Februari`` becomes ``Februari 2005``, as the generic parser writes month periods."""
    match = _MONTH_PERIOD.fullmatch(label)
    return label if match is None else f"{match.group(2).capitalize()} {match.group(1)}"


def _title_period(title: str) -> str:
    match = re.search(r"((?:19|20)\d{2})\s*[-–]\s*((?:19|20)\d{2})\s*$", title)
    _expect(match is not None, f"title {title!r} does not end in a year span")
    return f"{match.group(1)}-{match.group(2)}"


def _notes(grid: Grid, start: int) -> list[str]:
    return [
        " ".join(_text(grid, row, column) for column in range(3) if _text(grid, row, column))
        for row in range(start, len(grid))
        if _text(grid, row, 0) or _text(grid, row, 1)
    ]


# SP2020 long form, province and regency/city rows in one column.

_SP2020_PROVINCES = (
    "ACEH",
    "SUMATERA UTARA",
    "SUMATERA BARAT",
    "RIAU",
    "JAMBI",
    "SUMATERA SELATAN",
    "BENGKULU",
    "LAMPUNG",
    "BANGKA BELITUNG",
    "KEPULAUAN RIAU",
    "DKI JAKARTA",
    "JAWA BARAT",
    "JAWA TENGAH",
    "DI YOGYAKARTA",
    "JAWA TIMUR",
    "BANTEN",
    "BALI",
    "NUSA TENGGARA BARAT",
    "NUSA TENGGARA TIMUR",
    "KALIMANTAN BARAT",
    "KALIMANTAN TENGAH",
    "KALIMANTAN SELATAN",
    "KALIMANTAN TIMUR",
    "KALIMANTAN UTARA",
    "SULAWESI UTARA",
    "SULAWESI TENGAH",
    "SULAWESI SELATAN",
    "SULAWESI TENGGARA",
    "GORONTALO",
    "SULAWESI BARAT",
    "MALUKU",
    "MALUKU UTARA",
    "PAPUA BARAT",
    "PAPUA",
)
_SP2020_TYPOS = {"BENKULU": "BENGKULU"}


def _sp2020(measure: str, *, column_axis: str | None = None) -> Extractor:
    """A province is the next name in BPS order; the rows under it are its regencies and cities.

    Order matters because the regency ``GORONTALO`` follows the province of the same name. Three of the
    sheets spell the province row ``BENKULU``; it is stored as ``BENGKULU``.
    """

    def extract(path: Path) -> Extraction:
        sheet, grid = _sheet(path)
        header = next(
            (row for row in range(len(grid)) if _text(grid, row, 1).casefold().startswith(("provinsi", "kabupaten"))),
            None,
        )
        _expect(header is not None, "no Provinsi/Kabupaten/Kota header")
        title = next(_text(grid, row, 1) for row in range(header) if _text(grid, row, 1))
        columns = [column for column in range(2, _width(grid)) if _text(grid, header, column)]
        _expect(bool(columns), "no value columns")
        _expect(column_axis is not None or len(columns) == 1, f"expected one value column, found {len(columns)}")
        pending = list(_SP2020_PROVINCES)
        province: str | None = None
        drafts: list[ObservationDraft] = []
        for row in range(header + 1, len(grid)):
            label = _text(grid, row, 1)
            label = _SP2020_TYPOS.get(label, label)
            if not label or all(_cell(grid, row, column).blank for column in columns):
                continue
            if label == "INDONESIA":
                dimensions = [("Provinsi", label)]
            elif pending and label == pending[0]:
                province = pending.pop(0)
                dimensions = [("Provinsi", label)]
            else:
                _expect(province is not None, f"row {row} {label!r} comes before the first province")
                dimensions = [("Provinsi", province), ("Kabupaten/Kota", label)]
            for column in columns:
                cell = _cell(grid, row, column)
                if cell.blank:
                    continue
                extra = [(column_axis, _text(grid, header, column))] if column_axis else []
                drafts.append(
                    _draft(sheet, row, column, cell, measure=measure, period="LF SP2020", dimensions=dimensions + extra)
                )
        _expect(not pending, f"province {pending[0] if pending else ''} not found in BPS order")
        return drafts, title

    return extract


# Susenas food consumption, Bengkulu.


def _consumption(path: Path) -> Extraction:
    """Two stacked sections (consumption, then spending), each with a commodity column per group of years.

    An upper-case commodity row heads a commodity group. Its values are the group subtotal (zero throughout
    the consumption section, where quantities in different units are not added up). The spending section
    labels its 2024 block ``2023`` again, so a block that repeats the year on its left takes the next year,
    and the years must run 2018-2025.
    """
    sheet, grid = _sheet(path, "indo")
    starts = [row for row in range(len(grid)) if _text(grid, row, 0).startswith("Rata-rata")]
    _expect(len(starts) == 2, f"expected two section titles, found {len(starts)}")
    drafts: list[ObservationDraft] = []
    titles: list[str] = []
    for start, end in zip(starts, [*starts[1:], len(grid)], strict=True):
        title = _text(grid, start, 0)
        titles.append(title)
        measure = title.split(" Menurut")[0].strip()
        province = _text(grid, start + 1, 0).removeprefix("PROVINSI").strip()
        _expect(province == "BENGKULU", f"section at row {start} is for {province!r}")
        year_row, area_row, kind_row, bracket_row = start + 2, start + 3, start + 4, start + 5
        segments: list[tuple[int, dict[int, tuple[str, str, str]]]] = []
        years: list[str] = []
        year = area = ""
        for column in range(_width(grid)):
            head = _text(grid, year_row, column)
            if head and not re.fullmatch(r"(?:19|20)\d{2}", head):
                _expect("Komoditi" in head, f"unexpected header {head!r} at column {column}")
                segments.append((column, {}))
                year = area = ""
                continue
            _expect(bool(segments), "value columns before the first commodity column")
            if head:
                year = str(int(years[-1]) + 1) if years and head == years[-1] else head
                years.append(year)
                area = ""
            area = _text(grid, area_row, column) or area
            kind = _text(grid, kind_row, column).replace("\u2011", "-")
            bracket = _text(grid, bracket_row, column) or ("Rata-rata per Kapita" if kind.startswith("Rata") else "")
            if year and area and bracket:
                segments[-1][1][column] = (year, area, bracket)
        _expect(years == [str(year) for year in range(2018, 2026)], f"section at row {start} has years {years}")
        groups: dict[int, str] = {}
        for row in range(start + 6, end):
            for commodity_column, columns in segments:
                label = _text(grid, row, commodity_column)
                if not label:
                    continue
                cells = {column: _cell(grid, row, column) for column in columns}
                filled = {column: cell for column, cell in cells.items() if not cell.blank}
                if label.isupper():
                    groups[commodity_column] = label
                    if all(cell.number == 0 for cell in filled.values()):
                        continue
                    commodity = [("Kelompok Komoditi", label)]
                else:
                    group = groups.get(commodity_column)
                    commodity = [("Kelompok Komoditi", group)] if group else []
                    commodity.append(("Komoditi Makanan", label))
                for column, cell in filled.items():
                    period, area, bracket = columns[column]
                    dimensions = [
                        ("Provinsi", province),
                        *commodity,
                        ("Daerah", area),
                        ("Golongan Pengeluaran per Kapita Seminggu", bracket),
                    ]
                    drafts.append(
                        _draft(sheet, row, column, cell, measure=measure, period=period, dimensions=dimensions)
                    )
    return drafts, "\n".join(titles)


# Susenas household shares by province, one block of categories per year.


def _household_blocks(axis: str, first_year: int, last_year: int) -> Extractor:
    """Each year block ends at its ``Jumlah`` column. Only the first cell of a block names its year.

    Later cells of the year row were filled by dragging and repeat ``2012`` or ``2013``, so they are ignored.
    """

    def extract(path: Path) -> Extraction:
        sheet, grid = _sheet(path, "Sheet1")
        header = next((row for row in range(len(grid)) if _text(grid, row, 0) == "Provinsi"), None)
        _expect(header is not None, "no Provinsi header")
        categories = header + 1
        blocks: list[tuple[str, list[int]]] = []
        column = 1
        while column < _width(grid):
            if not _text(grid, categories, column):
                column += 1
                continue
            start = column
            while column < _width(grid) and _text(grid, categories, column) != "Jumlah":
                column += 1
            blocks.append((_text(grid, header, start), list(range(start, column + 1))))
            column += 1
        years = [label for label, _columns in blocks]
        expected = [str(year) for year in range(first_year, last_year + 1)]
        _expect(years == expected, f"block years {years} are not {first_year}-{last_year}")
        drafts: list[ObservationDraft] = []
        end = next((row for row in range(categories + 1, len(grid)) if _text(grid, row, 0) == "Catatan:"), len(grid))
        for row in range(categories + 1, end):
            province = _strip_footnote(_text(grid, row, 0))
            if not province:
                continue
            for period, columns in blocks:
                for column in columns:
                    cell = _cell(grid, row, column)
                    if cell.blank:
                        continue
                    category = _strip_footnote(_text(grid, categories, column))
                    drafts.append(
                        _draft(
                            sheet,
                            row,
                            column,
                            cell,
                            measure="Persentase Rumah Tangga",
                            period=period,
                            dimensions=[("Provinsi", province), (axis, category)],
                        )
                    )
        return drafts, "\n".join([_text(grid, 0, 0), *_notes(grid, end)])

    return extract


def _garden_trees(path: Path) -> Extraction:
    """Susenas 2013 module; the year is only in the catalog title."""
    sheet, grid = _sheet(path, "Sheet1")
    _expect(_text(grid, 2, 0) == "Provinsi", "no Provinsi header on row 2")
    axis = _text(grid, 2, 1)
    _expect(axis.startswith("Keberadaan Tanaman"), f"unexpected column header {axis!r}")
    drafts = [
        _draft(
            sheet,
            row,
            column,
            _cell(grid, row, column),
            measure="Persentase Rumah Tangga",
            period="2013",
            dimensions=[("Provinsi", _text(grid, row, 0)), (axis, _text(grid, 3, column))],
        )
        for row in range(4, len(grid))
        if _text(grid, row, 0)
        for column in (1, 2)
        if not _cell(grid, row, column).blank
    ]
    return drafts, _text(grid, 0, 0)


# International migration, 2022-2025.


def _migration_characteristics(path: Path) -> Extraction:
    """Label-only rows (``Jenis Kelamin``, ``Kelompok Umur``) name the axis of the rows under them."""
    sheet, grid = _sheet(path)
    title = _text(grid, 0, 0)
    period = _title_period(title)
    _expect(_text(grid, 2, 0) == "Karakteristik", "no Karakteristik header on row 2")
    columns = {column: _text(grid, 2, column) for column in (1, 2)}
    _expect(set(columns.values()) == {"Migran Masuk", "Migran Keluar"}, f"unexpected columns {columns}")
    drafts: list[ObservationDraft] = []
    axis: str | None = None
    notes_start = len(grid)
    for row in range(3, len(grid)):
        label = _text(grid, row, 0)
        if label == "Catatan:":
            notes_start = row
            break
        if not label:
            continue
        if all(_cell(grid, row, column).blank for column in columns):
            axis = label
            continue
        _expect(axis is not None, f"row {row} {label!r} has no characteristic above it")
        for column, direction in columns.items():
            drafts.append(
                _draft(
                    sheet,
                    row,
                    column,
                    _cell(grid, row, column),
                    measure="Jumlah Migran Internasional",
                    period=period,
                    dimensions=[(axis, label), ("Arah Migrasi", direction)],
                )
            )
    return drafts, "\n".join([title, *_notes(grid, notes_start)])


def _migration_top_provinces(path: Path) -> Extraction:
    sheet, grid = _sheet(path)
    title = _text(grid, 0, 0)
    period = _title_period(title)
    _expect(_text(grid, 2, 0) == "Provinsi", "no Provinsi header on row 2")
    measure = f"{_text(grid, 2, 1)} Internasional"
    _expect(measure in {"Migran Masuk Internasional", "Migran Keluar Internasional"}, f"unexpected {measure!r}")
    sexes = {column: _text(grid, 3, column) for column in (1, 2, 3)}
    _expect(sexes[1] == "Laki-laki" and sexes[2] == "Perempuan", f"unexpected sex columns {sexes}")
    drafts: list[ObservationDraft] = []
    notes_start = len(grid)
    for row in range(4, len(grid)):
        label = _text(grid, row, 0)
        if label == "Catatan:":
            notes_start = row
            break
        if not label:
            continue
        for column, sex in sexes.items():
            drafts.append(
                _draft(
                    sheet,
                    row,
                    column,
                    _cell(grid, row, column),
                    measure=measure,
                    period=period,
                    dimensions=[("Provinsi", label), ("Jenis Kelamin", sex)],
                )
            )
    return drafts, "\n".join([title, *_notes(grid, notes_start)])


# Dot-thousands tables. Excel read ``1.333`` as the decimal 1.333, so each value is checked against the
# row total before it is stored.


def _dot_thousands(cell: Cell, *, scale_integers: bool) -> float | None:
    """Read a count typed with dots between thousands.

    ``1.333`` (stored as the number 1.333) is 1 333, ``454.82`` is 454 820 with the trailing zero dropped,
    and ``1.121.190`` stays text. A whole number such as ``473`` is 473 000 when every value in the table is
    at least a thousand (``scale_integers``) and 473 otherwise.
    """
    if cell.number is not None:
        if cell.number != int(cell.number) or scale_integers:
            return round(cell.number * 1000)
        return cell.number
    text = cell.text.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+", text):
        return float(text.replace(".", ""))
    return None


def _counted(draft: ObservationDraft, value: float, source: str) -> ObservationDraft:
    """Keep the cell text as published and store the count it stands for."""
    return dataclasses.replace(draft, source_value=source, numeric_value=value, canonical_value=value)


def _near(parts: float, total: float) -> bool:
    return abs(parts - total) <= max(5.0, total * 0.001)


def _deaths(path: Path) -> Extraction:
    """A ``1`` in the unlabelled column after a value flags a relative standard error of 25-50%."""
    sheet, grid = _sheet(path, "21")
    title = _text(grid, 0, 0)
    _expect(title.endswith("Sejak 1 Juli 2022"), f"unexpected title {title!r}")
    causes = {column: _text(grid, 3, column) for column in range(1, _width(grid)) if _text(grid, 3, column)}
    total_column = next((column for column in range(1, _width(grid)) if _text(grid, 2, column) == "Jumlah"), None)
    _expect(len(causes) == 5 and total_column is not None, f"unexpected cause columns {causes}")
    columns = {**causes, total_column: "Jumlah"}
    drafts: list[ObservationDraft] = []
    notes_start = next(row for row in range(len(grid)) if _text(grid, row, 0) == "Catatan:")
    for row in range(4, notes_start):
        province = _text(grid, row, 0)
        if not province:
            continue
        values: dict[int, float] = {}
        for column, cause in columns.items():
            cell = _cell(grid, row, column)
            draft = _draft(
                sheet,
                row,
                column,
                cell,
                measure="Jumlah Kematian",
                period="Sejak 1 Juli 2022",
                dimensions=[("Provinsi", province), ("Penyebab Utama Kematian", cause)],
            )
            if draft.status_code is None:
                value = _dot_thousands(cell, scale_integers=False)
                _expect(value is not None, f"row {row} column {column} {cell.text!r} is not a count")
                values[column] = value
                draft = _counted(draft, value, cell.text)
            if _text(grid, row, column + 1) == "1" and column + 1 not in columns:
                draft = dataclasses.replace(draft, status_code=_RSE_FLAG)
            drafts.append(draft)
        if len(values) == len(columns):
            parts = sum(value for column, value in values.items() if column != total_column)
            _expect(_near(parts, values[total_column]), f"row {row} causes sum to {parts}, not the total")
    return drafts, "\n".join([title, *_notes(grid, notes_start)])


# Sakernas, year blocks laid out in pairs.


def _pair_period(label: str) -> str:
    return _month_period(label.strip())


def _paired_blocks(anchor: str, column_axis: str, *, legend_title: str | None = None) -> Extractor:
    """Blocks of rows under an ``anchor`` header, each with two periods side by side ending in ``Total``.

    With ``legend_title`` the column headers are codes explained by a legend under the table.
    """

    def extract(path: Path) -> Extraction:
        sheet, grid = _sheet(path)
        title = _text(grid, 0, 0)
        legend = _legend(grid, legend_title) if legend_title else {}
        headers = [row for row in range(len(grid)) if _text(grid, row, 0) == anchor]
        _expect(bool(headers), f"no {anchor!r} header")
        drafts: list[ObservationDraft] = []
        for header in headers:
            categories = header + 2
            totals = [column for column in range(_width(grid)) if _text(grid, categories, column) == "Total"]
            _expect(len(totals) == 2, f"block at row {header} has {len(totals)} Total columns")
            blocks = [(1, totals[0]), (totals[0] + 1, totals[1])]
            periods = [_text(grid, header, start) for start, _end in blocks]
            if not periods[1] and periods[0].endswith("Februari"):
                # The August label is missing in 2006 and 2007; the totals match August in the sector table.
                periods[1] = periods[0].replace("Februari", "Agustus")
            _expect(all(periods), f"block at row {header} has an unlabelled period")
            row = categories + 1
            while row < len(grid) and _text(grid, row, 0):
                status = _text(grid, row, 0)
                for (start, end), period in zip(blocks, periods, strict=True):
                    for column in range(start, end + 1):
                        cell = _cell(grid, row, column)
                        if cell.blank:
                            continue
                        category = _text(grid, categories, column)
                        member = legend.get(category, category) if legend else category
                        drafts.append(
                            _draft(
                                sheet,
                                row,
                                column,
                                cell,
                                measure=title,
                                period=_pair_period(period),
                                dimensions=[(anchor, status), (column_axis, member)],
                            )
                        )
                if status == "Total":
                    break
                row += 1
        notes_start = max(headers) + 1
        while notes_start < len(grid) and _text(grid, notes_start, 0) != "Total":
            notes_start += 1
        return drafts, "\n".join([title, *_notes(grid, notes_start + 1)])

    return extract


def _legend(grid: Grid, legend_title: str) -> dict[str, str]:
    """Read ``1 Pertanian, ...`` lines under ``legend_title`` into code to name, keeping ``Total``."""
    start = next((row for row in range(len(grid)) if _text(grid, row, 0) == legend_title), None)
    _expect(start is not None, f"no legend {legend_title!r}")
    legend: dict[str, str] = {}
    for row in range(start + 1, len(grid)):
        match = re.fullmatch(r"(\d+)\s+(.+)", _text(grid, row, 0))
        if match is None:
            break
        legend[match.group(1)] = match.group(2)
    _expect(len(legend) >= 10, f"legend {legend_title!r} has {len(legend)} codes")
    return legend


def _hours_by_age(path: Path) -> Extraction:
    """Every value is in thousands notation; each row of hours bands must add up to its ``Total``.

    Seven cells hold what Excel turned into date serials (``19370.000046296296``). They are kept as text
    without a number, and the notes give the value the row total implies when only one cell of the row is
    unreadable.
    """
    sheet, grid = _sheet(path, "Sheet1")
    title = _text(grid, 0, 0)
    headers = [row for row in range(len(grid)) if _text(grid, row, 0) == "Golongan Umur"]
    _expect(len(headers) == 17, f"expected 17 yearly blocks (2008-2024), found {len(headers)}")
    drafts: list[ObservationDraft] = []
    problems: list[str] = []
    for header in headers:
        categories = header + 2
        totals = [column for column in range(_width(grid)) if _text(grid, categories, column) == "Total"]
        _expect(len(totals) == 2, f"block at row {header} has {len(totals)} Total columns")
        blocks = [(1, totals[0]), (totals[0] + 1, totals[1])]
        for start, end in blocks:
            period = _month_period(_text(grid, header, start))
            for row in range(categories + 1, categories + 12):
                age = _text(grid, row, 0)
                _expect(bool(age), f"row {row} has no age group")
                cells = {column: _cell(grid, row, column) for column in range(start, end + 1)}
                values = {column: _dot_thousands(cell, scale_integers=True) for column, cell in cells.items()}
                total = values[end]
                unreadable = [column for column, value in values.items() if value is None]
                parts = sum(value for column, value in values.items() if column != end and value is not None)
                for column in unreadable:
                    implied = ""
                    if len(unreadable) == 1:
                        implied = (
                            f"; the row implies {parts:.0f}"
                            if column == end
                            else (f"; the row total implies {total - parts:.0f}")
                        )
                    band = _text(grid, categories, column)
                    problems.append(f"{period} {age} {band}: {cells[column].text} is unreadable{implied}")
                if total is not None and unreadable:
                    _expect(parts <= total, f"row {row} {period} bands without unreadable cells exceed the total")
                elif total is not None:
                    _expect(_near(parts, total), f"row {row} {period} bands sum to {parts}, not {total}")
                for column, value in values.items():
                    band = _text(grid, categories, column).replace("*)", "")
                    draft = _draft(
                        sheet,
                        row,
                        column,
                        cells[column],
                        measure=title,
                        period=period,
                        dimensions=[("Golongan Umur", age), ("Jumlah Jam Kerja Seluruhnya", band)],
                    )
                    if value is None:
                        drafts.append(dataclasses.replace(draft, numeric_value=None, canonical_value=None))
                    else:
                        drafts.append(_counted(draft, value, cells[column].text))
    notes_start = max(headers) + 14
    return drafts, "\n".join([title, *_notes(grid, notes_start), *problems])


def _sectors(path: Path) -> Extraction:
    """Two tables: 9 sectors for 1986-2017, then 17 categories for 2011-2024.

    The overlapping years use different classifications, so each table gets its own sector axis. A month
    column with no year of its own that repeats a month of the year on its left belongs to the next year.
    """
    sheet, grid = _sheet(path, "bpsfile2")
    headers = [row for row in range(len(grid)) if _text(grid, row, 0) == "No."]
    _expect(len(headers) == 2, f"expected two tables, found {len(headers)}")
    axes = ["Lapangan Pekerjaan Utama (9 sektor)", "Lapangan Pekerjaan Utama (17 kategori)"]
    measure = "Penduduk 15 Tahun Ke Atas yang Bekerja"
    drafts: list[ObservationDraft] = []
    for header, axis in zip(headers, axes, strict=True):
        periods: dict[int, str] = {}
        year = ""
        seen: set[tuple[str, str]] = set()
        for column in range(2, _width(grid)):
            label = re.sub(r"\*+\)?$", "", _text(grid, header, column))
            month = _text(grid, header + 1, column)
            if label:
                year = label
            elif month and (year, month) in seen:
                _expect(
                    str(int(year) + 1) not in {_text(grid, header, c) for c in range(_width(grid))},
                    f"column {column} repeats {month} {year}",
                )
                year = str(int(year) + 1)
            if not year:
                continue
            seen.add((year, month))
            periods[column] = f"{month} {year}" if month else year
        row = header + 1
        while row < len(grid) and _text(grid, row, 1) != "Total":
            row += 1
        _expect(row < len(grid), f"table at row {header} has no Total row")
        for data_row in range(header + 2, row + 1):
            sector = _text(grid, data_row, 1)
            if not sector:
                continue
            code = _text(grid, data_row, 0)
            member = f"{code} {sector}" if code else sector
            for column, period in periods.items():
                cell = _cell(grid, data_row, column)
                if cell.blank:
                    continue
                drafts.append(
                    _draft(sheet, data_row, column, cell, measure=measure, period=period, dimensions=[(axis, member)])
                )
    notes_start = next(row for row in range(headers[1], len(grid)) if _text(grid, row, 1) == "Total") + 1
    titles = [_text(grid, header - 2, 0) for header in headers]
    return drafts, "\n".join([*titles, *_notes(grid, notes_start)])


EXTRACTORS: dict[str, Extractor] = {
    "MjIyMSMx": _sp2020("Angka Kematian Anak (CMR)"),
    "MjIxMyMx": _sp2020("Angka Kelahiran Kasar (CBR)"),
    "MjIyMiMx": _sp2020("Angka Kematian Balita (U5MR)"),
    "MjIyMCMx": _sp2020("Angka Kematian Bayi (IMR)"),
    "MjIxNSMx": _sp2020("Angka Kelahiran Menurut Kelompok Umur Ibu (ASFR)", column_axis="Kelompok Umur Ibu"),
    "MjE3MyMx": _consumption,
    "MTM2MyMx": _household_blocks("Fasilitas Tempat Buang Air Besar", 2000, 2021),
    "MTM2MSMx": _household_blocks("Sumber Air Minum", 2000, 2016),
    "MTM1MyMx": _garden_trees,
    "MjMxNCMx": _migration_characteristics,
    "MjMwNCMx": _migration_top_provinces,
    "MjMwNyMx": _migration_top_provinces,
    "MjI5OCMx": _deaths,
    "OTcwIzE=": _sectors,
    "OTc3IzE=": _paired_blocks(
        "Status Pekerjaan Utama", "Lapangan Pekerjaan Utama", legend_title="kode Lapangan Pekerjaan Utama:"
    ),
    "MTkxMCMx": _paired_blocks(
        "Status Pekerjaan Utama", "Lapangan Pekerjaan Utama", legend_title="kode Lapangan Pekerjaan Utama:"
    ),
    "MTkzNSMx": _paired_blocks("Status Pekerjaan Utama", "Pendidikan Tertinggi yang Ditamatkan"),
    "OTcxIzE=": _hours_by_age,
}
