"""Parse official BPS static workbooks into observations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft, ParseResult
from tilik_agent.ingestion.parsing import (
    canonical_member,
    classify_dimension_type,
    convert_value,
    duplicate_reason,
    format_excel_number,
    infer_unit,
    is_age_band,
    is_status_token,
    is_year_token,
    normalize_label,
    stable_code,
    year_in_text,
)


@dataclass(frozen=True)
class Cell:
    """One workbook cell, keeping both the display text and a parsed number."""

    text: str
    number: float | None

    @property
    def blank(self) -> bool:
        return self.text == "" and self.number is None


def parse_workbook(path: Path, *, fallback_title: str) -> ParseResult:
    """Parse the Indonesian sheet of a static workbook.

    Layouts that do not expose a year column or a single-year section are kept raw.
    """
    sheets = load_sheets(path)
    selected = _select_sheets(sheets)
    if not selected:
        return ParseResult(needs_review=True, review_reason="Workbook contained no sheets")
    parsed: list[ParseResult] = []
    for name, grid in selected:
        result = _parse_sheet(name, grid, fallback_title=fallback_title)
        if result.needs_review:
            return result
        parsed.append(result)
    observations = _drop_repeated_cells([draft for result in parsed for draft in result.observations])
    notes = "\n".join(result.notes for result in parsed if result.notes)
    mixed = _mixed_thousands(observations)
    if mixed:
        return ParseResult(needs_review=True, review_reason=mixed, notes=notes or None)
    duplicate = duplicate_reason(observations)
    if duplicate:
        return ParseResult(needs_review=True, review_reason=duplicate, notes=notes or None)
    if not observations:
        return ParseResult(needs_review=True, review_reason="Workbook produced no observations")
    return ParseResult(observations=observations, notes=notes or None)


def _drop_repeated_cells(observations: list[ObservationDraft]) -> list[ObservationDraft]:
    """Keep the first of cells that repeat the same value at the same coordinates.

    A sheet can print one figure twice, as in ``SLTP`` under both a detailed and a grouped education
    block. The same coordinates with a different value still reach the duplicate check.
    """
    seen: set[tuple[object, ...]] = set()
    kept: list[ObservationDraft] = []
    for draft in observations:
        coordinates = (
            draft.measure_source_code,
            draft.period_source_code,
            frozenset((d.source_code, d.member_source_code) for d in draft.dimensions),
            draft.source_value,
        )
        if coordinates in seen:
            continue
        seen.add(coordinates)
        kept.append(draft)
    return kept


def _mixed_thousands(observations: list[ObservationDraft]) -> str | None:
    """Flag a workbook where Excel read some dot-thousands values as decimals.

    When ``1.108.248`` stays text beside a numeric ``59.665``, the typist meant 59 665, but the cell holds
    59.665. Nothing in the file tells the two apart, so no number is stored.
    """
    grouped = next((d.source_value for d in observations if _DOT_THOUSANDS_MULTI.fullmatch(d.source_value)), None)
    decimal = next(
        (
            d.source_value
            for d in observations
            if d.numeric_value is not None and re.fullmatch(r"[1-9]\d{0,2}\.\d{3}", d.source_value)
        ),
        None,
    )
    if grouped is None or decimal is None:
        return None
    return f"Workbook mixes dot-thousands text ({grouped}) with numbers Excel stored as decimals ({decimal})"


def _parse_sheet(sheet_name: str, grid: list[list[Cell]], *, fallback_title: str) -> ParseResult:
    title = _sheet_title(grid) or fallback_title
    year_layout = _parse_year_columns(sheet_name, grid, title)
    if year_layout is not None:
        return year_layout
    row_layout = _parse_year_rows(sheet_name, grid, title)
    if row_layout is not None:
        return row_layout
    section_layout = _parse_year_sections(sheet_name, grid, title)
    if section_layout is not None:
        return section_layout
    return ParseResult(
        needs_review=True,
        review_reason=f"Sheet {sheet_name} has no year columns or single-year sections",
    )


def _parse_year_columns(sheet_name: str, grid: list[list[Cell]], title: str) -> ParseResult | None:
    """Parse blocks whose header has a row of years.

    Header rows above the year row and sub-header rows below it both qualify columns. When sub-headers
    exist, a year spans every column up to the next year. A caption row (one label, alone on its row,
    above a stub label) qualifies the whole block.
    """
    year_headers = _year_header_indexes(grid)
    if not year_headers:
        return None
    observations: list[ObservationDraft] = []
    notes: list[str] = []
    titled_blocks = False
    for position, header_index in enumerate(year_headers):
        year_row = grid[header_index]
        first_year = next(index for index, cell in enumerate(year_row) if _period_token(cell) is not None)
        unreadable = _unreadable_period(year_row, first_year)
        if unreadable is not None:
            return ParseResult(
                needs_review=True,
                review_reason=f"Sheet {sheet_name} period header {unreadable!r} is not a readable period",
            )
        above = _headers_above(grid, header_index, first_year)
        caption_indexes = _caption_indexes(above, year_row, first_year)
        captions = [row_text for index, row in above if index in caption_indexes for row_text in _texts(row)]
        header_rows = [(index, row) for index, row in above if index not in caption_indexes]
        if position == 0:
            titled_blocks = bool(captions) and normalize_label(captions[0]) == normalize_label(title)
        block_title = title
        if titled_blocks and captions:
            block_title = captions.pop(0)
        data_end = _next_block_start(grid, year_headers, position, first_year)
        below = _headers_below(grid, header_index, data_end, first_year)
        data_start = header_index + 1 + len(below)
        data_rows = list(enumerate(grid[data_start:data_end], start=data_start))
        year_by_column = _year_by_column(year_row, first_year, spread=bool(below))
        table_rows = [row for _index, row in data_rows if not _is_caption_row(row, first_year)]
        stub_indexes = _stub_indexes(table_rows, year_row, first_year)
        indicator_index = next((index for index in stub_indexes if _is_indicator(year_row[index])), None)
        dimension_stubs = [index for index in stub_indexes if index != indicator_index]
        column_headers = [*header_rows, (header_index, year_row), *below]
        stub_labels, label_rows = _stub_labels(column_headers, dimension_stubs, first_year)
        inner_stubs = _inner_stubs(year_row, table_rows, first_year)
        for inner in inner_stubs:
            stub_labels[inner] = stub_labels[dimension_stubs[-1]] if dimension_stubs else year_row[inner].text.strip()
        segments = _stub_segments(dimension_stubs, inner_stubs, year_by_column)
        labelled = [*dimension_stubs, *inner_stubs, *([indicator_index] if indicator_index is not None else [])]
        grouped = {index for index in labelled if _has_repeated_labels(table_rows, index)}
        filled = _fill_headers(column_headers, first_year)
        taken = {stable_code(label) for label in stub_labels.values()}
        label_headers = [
            (header, row) for header, row in zip(column_headers, filled, strict=True) if header[0] != header_index
        ]
        column_dimensions = {
            column: _column_dimensions(label_headers, column, first_year, label_rows, taken)
            for column in year_by_column
        }
        caption_dimensions = _caption_dimensions(captions)
        carried: dict[int, str] = {}
        groups: dict[int, str] = {}
        row_caption: str | None = None
        for position, (row_index, row) in enumerate(data_rows):
            if _is_blank_row(row):
                carried.clear()
                continue
            if _is_note_row(row):
                notes.append(row[0].text)
                continue
            if _is_year_header(row) or _is_column_number_row(row):
                continue
            if _is_caption_row(row, first_year):
                row_caption = _texts(row)[0].rstrip(":").strip()
                continue
            for index in grouped:
                label = _stub_text(row, index)
                if label and _is_group_label(label) and groups.get(index) != label:
                    groups[index] = label
                    row_caption = None
            label_only = not any(_cell_at(row, column).text.strip() for column in year_by_column)
            if label_only and any(_stub_text(row, index) for index in grouped):
                following = data_rows[position + 1][1] if position + 1 < len(data_rows) else []
                if not any(_is_group_label(_stub_text(row, index)) for index in grouped) and any(
                    _cell_at(following, column).text.strip() for column in year_by_column
                ):
                    row_caption = next(_stub_text(row, index) for index in grouped if _stub_text(row, index))
                continue
            for index in dimension_stubs[:-1]:
                if _stub_text(row, index):
                    carried[index] = _stub_text(row, index)
            measure_label = row[indicator_index].text.strip() if indicator_index is not None else block_title
            if indicator_index is not None and not measure_label:
                continue
            for stubs, columns in segments:
                if indicator_index is None and not any(_stub_text(row, index) for index in stubs):
                    continue
                leading = stubs is dimension_stubs
                qualified = [*stubs, *([indicator_index] if leading and indicator_index is not None else [])]
                row_dimensions = _row_dimensions(
                    row,
                    stubs,
                    stub_labels,
                    carried=carried if leading else {},
                    groups={index: groups[index] for index in qualified if index in groups},
                    caption=row_caption,
                )
                for column in columns:
                    cell = row[column] if column < len(row) else Cell("", None)
                    if cell.blank:
                        continue
                    observations.append(
                        value_draft(
                            sheet_name=sheet_name,
                            row_index=row_index,
                            column=column,
                            measure_label=measure_label,
                            period=year_by_column[column],
                            cell=cell,
                            dimensions=tuple(row_dimensions + column_dimensions[column] + caption_dimensions),
                            unit_label=measure_label,
                        )
                    )
    if not observations:
        return ParseResult(needs_review=True, review_reason=f"Sheet {sheet_name} year columns had no values")
    return ParseResult(observations=observations, notes="\n".join(notes) or None)


def _texts(row: list[Cell]) -> list[str]:
    return [cell.text.strip() for cell in row if cell.text.strip()]


def _caption_indexes(
    above: list[tuple[int, list[Cell]]],
    year_row: list[Cell],
    first_year: int,
) -> set[int]:
    """Rows holding one label in the stub area, with another label below it in the same column."""
    captions: set[int] = set()
    lower_rows = [row for _index, row in above] + [year_row]
    for position, (index, row) in enumerate(above):
        filled = [column for column, cell in enumerate(row) if not cell.blank]
        if len(filled) != 1 or filled[0] >= first_year:
            continue
        column = filled[0]
        if any(column < len(lower) and lower[column].text.strip() for lower in lower_rows[position + 1 :]):
            captions.add(index)
    return captions


def _caption_dimensions(captions: list[str]) -> list[DimensionDraft]:
    """One ``bagian`` axis per caption level, counted from the row nearest the years."""
    drafts: list[DimensionDraft] = []
    for level, caption in enumerate(reversed(captions), start=1):
        label = "bagian" if level == 1 else f"bagian {level}"
        drafts.append(
            DimensionDraft(
                source_code=stable_code(label),
                original_label=label,
                member_source_code=stable_code(caption),
                member_label=caption,
                dimension_type=classify_dimension_type(label),
                canonical_code=canonical_member(caption),
            )
        )
    return drafts


def _headers_below(
    grid: list[list[Cell]],
    year_index: int,
    data_end: int,
    first_year: int,
) -> list[tuple[int, list[Cell]]]:
    """Label rows between the year row and the first data row.

    A label row either leaves the leading stub cell blank, or holds only digit-free text in the year area
    (the stub cell then carries the stub's own header, such as ``Provinsi``).
    """
    rows = grid[year_index + 1 : data_end]
    lead = _lead_stub(rows, first_year)
    if lead is None:
        return []
    headers: list[tuple[int, list[Cell]]] = []
    for offset, row in enumerate(rows[:4]):
        lead_blank = lead >= len(row) or row[lead].blank
        if lead_blank and not _is_label_row(row, first_year):
            break
        if not lead_blank and not _is_pure_label_row(row, first_year):
            break
        headers.append((year_index + 1 + offset, row))
    return headers


def _is_pure_label_row(row: list[Cell], first_year: int) -> bool:
    cells = [cell for cell in row[first_year:] if not cell.blank]
    return len(cells) >= 2 and all(cell.number is None and not re.search(r"\d", cell.text) for cell in cells)


def _is_column_number_row(row: list[Cell]) -> bool:
    """A row such as ``(1) (2) (3)`` that numbers the columns. Excel may store ``(1)`` as the number -1."""
    cells = [cell for cell in row if not cell.blank]
    return bool(cells) and all(
        re.fullmatch(r"\(\d+\)", cell.text.strip())
        or (cell.number is not None and cell.number < 0 and float(cell.number).is_integer())
        for cell in cells
    )


def _lead_stub(rows: list[list[Cell]], first_year: int) -> int | None:
    counts = [sum(1 for row in rows if column < len(row) and row[column].text.strip()) for column in range(first_year)]
    if not counts or max(counts) < 1:
        return None
    return counts.index(max(counts))


def _is_label_row(row: list[Cell], first_year: int) -> bool:
    labels = 0
    values = 0
    for cell in row[first_year:]:
        if cell.blank:
            continue
        if cell.number is not None or is_status_token(cell.text, letter_flags=False) or _text_number(cell.text):
            values += 1
        else:
            labels += 1
    return labels > values


def _year_by_column(year_row: list[Cell], first_year: int, *, spread: bool) -> dict[int, str]:
    """Map value columns to a period. ``spread`` carries a period right until the next label in the year row."""
    periods: dict[int, str] = {}
    current: str | None = None
    headed = _row_periods(year_row, first_year)
    for column in range(first_year, len(year_row)):
        cell = year_row[column]
        period = headed.get(column)
        if period is not None:
            current = period
        elif not cell.blank:
            current = None
        if period is not None or (spread and current is not None):
            periods[column] = current if period is None else period
    return periods


_YEAR = r"(?:19|20)\d{2}"
_FOOTNOTED_YEAR = re.compile(rf"({_YEAR})(?:\s+\d\)?|\s*\d\)|\s*\*+\)?|\s*r)")
_YEAR_RANGE = re.compile(rf"({_YEAR})\s*([-–/])\s*({_YEAR})(?:\s*\d)?")
_CENSUS_PERIOD = re.compile(rf"((?:LF\s*)?(?:SP|SUPAS))\s*({_YEAR})", re.IGNORECASE)
_MONTH_YEAR = re.compile(rf"([A-Za-z]+)\.?\s+({_YEAR})")
_YEAR_MONTH = re.compile(rf"({_YEAR})\s+([A-Za-z]+)\.?")
_MONTHS = {
    **dict.fromkeys(["januari", "jan"], "Januari"),
    **dict.fromkeys(["februari", "feb"], "Februari"),
    **dict.fromkeys(["maret", "mar"], "Maret"),
    **dict.fromkeys(["april", "apr"], "April"),
    "mei": "Mei",
    **dict.fromkeys(["juni", "jun"], "Juni"),
    **dict.fromkeys(["juli", "jul"], "Juli"),
    **dict.fromkeys(["agustus", "agu", "agt", "ags"], "Agustus"),
    **dict.fromkeys(["september", "sept", "sep"], "September"),
    **dict.fromkeys(["oktober", "okt"], "Oktober"),
    **dict.fromkeys(["november", "nov", "nop"], "November"),
    **dict.fromkeys(["desember", "des"], "Desember"),
}


def _row_periods(year_row: list[Cell], first_year: int) -> dict[int, str]:
    """Periods of the year-row cells. A marked year beside the same unmarked year (``2011`` and ``2011*``,
    a revised series) keeps its marker, so both columns survive."""
    periods = {
        column: period
        for column in range(first_year, len(year_row))
        if (period := _period_token(year_row[column])) is not None
    }
    plain = {period for column, period in periods.items() if _printed(year_row[column]) == period}
    for column, period in periods.items():
        printed = _printed(year_row[column])
        if printed != period and period in plain:
            periods[column] = printed
    return periods


def _printed(cell: Cell) -> str:
    return cell.text if cell.number is not None else " ".join(cell.text.split())


def _period_token(cell: Cell) -> str | None:
    """Return the period a header cell names, as published.

    A year (``2020``), a census wave (``SP1971``, ``LF SP2020``), a year range (``1971-1980``), an academic
    year (``2013/2014``), or a month and year (``Sept 2011`` and ``2011 Sept`` become ``September 2011``). A footnote or
    revision marker after a year or range (``2008 2``, ``2000 1)``, ``2011*``, ``2013 r``, ``2010-20151``)
    is dropped.
    """
    year = is_year_token(cell.text, cell.number)
    if year is not None:
        return str(year)
    if cell.number is not None:
        return None
    text = " ".join(cell.text.split())
    if match := _FOOTNOTED_YEAR.fullmatch(text):
        return match.group(1)
    if (match := _YEAR_RANGE.fullmatch(text)) and match.group(1) < match.group(3):
        separator = "/" if match.group(2) == "/" else "-"
        return f"{match.group(1)}{separator}{match.group(3)}"
    if match := _CENSUS_PERIOD.fullmatch(text):
        return f"{' '.join(match.group(1).upper().split())}{match.group(2)}".replace("LFSP", "LF SP")
    if (match := _MONTH_YEAR.fullmatch(text)) and match.group(1).casefold() in _MONTHS:
        return f"{_MONTHS[match.group(1).casefold()]} {match.group(2)}"
    if (match := _YEAR_MONTH.fullmatch(text)) and match.group(2).casefold() in _MONTHS:
        return f"{_MONTHS[match.group(2).casefold()]} {match.group(1)}"
    return None


def _unreadable_period(year_row: list[Cell], first_year: int) -> str | None:
    """A year-area header that mentions a year but is not a period, so its column would be silently dropped."""
    for cell in year_row[first_year:]:
        if cell.number is None and _period_token(cell) is None and year_in_text(cell.text):
            return cell.text.strip()
    return None


def _year_header_indexes(grid: list[list[Cell]]) -> list[int]:
    return [index for index, row in enumerate(grid) if _is_year_header(row) or _is_sparse_year_header(grid, index)]


def _is_year_header(row: list[Cell]) -> bool:
    year_count = sum(_period_token(cell) is not None for cell in row)
    nonempty = sum(not cell.blank for cell in row)
    return year_count >= 3 and year_count * 2 >= nonempty


def _is_sparse_year_header(grid: list[list[Cell]], index: int) -> bool:
    """A year row with one or two periods, such as ``Komoditi | 2024``.

    Only labels sit left of the first period and nothing but periods to its right. The rows above it, up to
    the previous table's data or notes, leave the value columns empty, and either a blank row or a title
    sits directly above. A data row whose only value looks like a year fails that test: a column header or
    another data row sits above it.
    """
    row = grid[index]
    periods = [column for column, cell in enumerate(row) if _period_token(cell) is not None]
    if not periods:
        return False
    first = periods[0]
    if any(cell.number is not None for cell in row[:first]):
        return False
    if any(not cell.blank and column not in periods for column, cell in enumerate(row) if column > first):
        return False
    separated = index == 0 or _is_blank_row(grid[index - 1])
    for above in reversed(grid[:index]):
        if _is_data_row(above) or _is_footnote_row(above):
            break
        if any(not cell.blank for cell in above[first:]):
            return False
        separated = separated or not _is_blank_row(above)
    return separated


def _is_footnote_row(row: list[Cell]) -> bool:
    texts = _texts(row)
    return bool(texts) and normalize_label(texts[0]).startswith(("sumber", "catatan", "keterangan", "source", "note"))


def _headers_above(grid: list[list[Cell]], year_index: int, first_year: int) -> list[tuple[int, list[Cell]]]:
    collected: list[tuple[int, list[Cell]]] = []
    index = year_index - 1
    while index >= 0 and len(collected) < 4:
        row = grid[index]
        if all(cell.blank for cell in row):
            break
        if any(not cell.blank for cell in row[first_year:]) or any(cell.text.strip() for cell in row[:first_year]):
            collected.append((index, row))
        index -= 1
    return list(reversed(collected))


def _next_block_start(grid: list[list[Cell]], year_headers: list[int], position: int, first_year: int) -> int:
    if position + 1 >= len(year_headers):
        return len(grid)
    next_headers = _headers_above(grid, year_headers[position + 1], first_year)
    if next_headers:
        return next_headers[0][0]
    return year_headers[position + 1]


def _parse_year_rows(sheet_name: str, grid: list[list[Cell]], title: str) -> ParseResult | None:
    """Parse a table whose ``Tahun`` stub column holds the period of each row.

    Columns left of ``Tahun`` are row dimensions, carried down over merged cells. Each column to its right
    is a measure named by its header path.
    """
    located = _year_row_axis(grid)
    if located is None:
        return None
    header_index, period_column = located
    first_data = next(
        index for index in range(header_index + 1, len(grid)) if _period_token(_cell_at(grid[index], period_column))
    )
    headers = [(index, grid[index]) for index in range(header_index, first_data) if not _is_blank_row(grid[index])]
    filled = _fill_headers(headers, period_column)
    width = max(len(row) for row in grid)
    paths = [_header_path(filled, column) for column in range(width)]
    stub_labels = {column: paths[column] or "baris" for column in range(period_column)}
    observations: list[ObservationDraft] = []
    notes: list[str] = []
    carried: dict[int, str] = {}
    for row_index in range(first_data, len(grid)):
        row = grid[row_index]
        if _is_footnote_row(row):
            notes.extend(" ".join(_texts(note)) for note in grid[row_index:] if _texts(note))
            break
        values = [column for column in range(period_column + 1, len(row)) if not row[column].blank]
        if not values:
            continue
        period = _period_token(_cell_at(row, period_column))
        if period is None:
            return ParseResult(needs_review=True, review_reason=f"Sheet {sheet_name} row {row_index} has no period")
        for column in range(period_column):
            if _stub_text(row, column):
                carried[column] = _stub_text(row, column)
        dimensions = tuple(dimension_draft(stub_labels[column], label) for column, label in sorted(carried.items()))
        for column in values:
            measure_label = paths[column] or title
            observations.append(
                value_draft(
                    sheet_name=sheet_name,
                    row_index=row_index,
                    column=column,
                    measure_label=measure_label,
                    period=period,
                    cell=row[column],
                    dimensions=dimensions,
                    unit_label=measure_label,
                )
            )
    if not observations:
        return None
    return ParseResult(observations=observations, notes="\n".join([title, *notes]))


def _year_row_axis(grid: list[list[Cell]]) -> tuple[int, int] | None:
    """Find a ``Tahun`` header whose column below holds a period on most data rows."""
    for index, row in enumerate(grid):
        for column, cell in enumerate(row):
            if normalize_label(cell.text) != "tahun":
                continue
            below = [lower for lower in grid[index + 1 :] if any(c.number is not None for c in lower[column + 1 :])]
            periods = sum(_period_token(_cell_at(lower, column)) is not None for lower in below)
            if periods >= 3 and periods * 2 > len(below):
                return index, column
    return None


def _cell_at(row: list[Cell], column: int) -> Cell:
    return row[column] if column < len(row) else Cell("", None)


def _is_blank_row(row: list[Cell]) -> bool:
    return all(cell.blank for cell in row)


def _parse_year_sections(sheet_name: str, grid: list[list[Cell]], title: str) -> ParseResult | None:
    titles = [index for index, row in enumerate(grid) if _section_title_year(row) is not None]
    if not titles:
        return None
    observations: list[ObservationDraft] = []
    for position, start in enumerate(titles):
        year = _section_title_year(grid[start])
        end = titles[position + 1] if position + 1 < len(titles) else len(grid)
        if year is None:
            continue
        block = grid[start + 1 : end]
        header_rows: list[list[Cell]] = []
        data_rows: list[tuple[int, list[Cell]]] = []
        for offset, row in enumerate(block):
            if all(cell.blank for cell in row):
                continue
            absolute = start + 1 + offset
            if not data_rows and not _is_data_row(row):
                header_rows.append(row)
                continue
            if _is_data_row(row):
                data_rows.append((absolute, row))
        if not header_rows or not data_rows:
            return ParseResult(needs_review=True, review_reason=f"Sheet {sheet_name} section {year} has no grid")
        width = max(len(row) for row in header_rows + [item[1] for item in data_rows])
        filled = [_forward_fill(_pad(row, width)) for row in header_rows]
        paths = [_header_path(filled, column) for column in range(width)]
        occupied = {
            column for column in range(width) if any(column < len(row) and not row[column].blank for row in header_rows)
        }
        if any(is_age_band(path) for path in paths if path):
            return ParseResult(
                needs_review=True,
                review_reason=f"Sheet {sheet_name} column headers are categories without a year axis",
            )
        stub = _section_stub(paths, data_rows)
        if stub is None:
            return ParseResult(needs_review=True, review_reason=f"Sheet {sheet_name} section {year} has no row label")
        for row_index, row in data_rows:
            padded = _pad(row, width)
            member_label = padded[stub].text.strip()
            if not member_label:
                continue
            member = DimensionDraft(
                source_code=stable_code(paths[stub] or "baris"),
                original_label=paths[stub] or "baris",
                member_source_code=stable_code(member_label),
                member_label=member_label,
                dimension_type=classify_dimension_type(paths[stub] or "baris"),
                canonical_code=canonical_member(member_label),
            )
            code_dimensions = _code_dimensions(paths, occupied, padded)
            for column, path in enumerate(paths):
                if column not in occupied or column == stub or not path:
                    continue
                if _is_index_header(path) or _is_code_header(path):
                    continue
                cell = padded[column]
                if cell.blank:
                    continue
                observations.append(
                    value_draft(
                        sheet_name=sheet_name,
                        row_index=row_index,
                        column=column,
                        measure_label=path,
                        period=str(year),
                        cell=cell,
                        dimensions=(member, *code_dimensions),
                        unit_label=path,
                    )
                )
    if not observations:
        return None
    return ParseResult(observations=observations, notes=title)


def value_draft(
    *,
    sheet_name: str,
    row_index: int,
    column: int,
    measure_label: str,
    period: str,
    cell: Cell,
    dimensions: tuple[DimensionDraft, ...],
    unit_label: str,
) -> ObservationDraft:
    """Build the observation for one workbook cell. ``unit_label`` is the text the unit is inferred from."""
    source_text, numeric, status = _interpret_static_cell(cell)
    unit = infer_unit(unit_label)
    canonical_numeric, canonical_unit = convert_value(numeric, unit, status)
    return ObservationDraft(
        measure_source_code=stable_code(measure_label),
        measure_label=measure_label,
        measure_unit=unit,
        measure_definition=None,
        measure_decimals=None,
        measure_note=None,
        source_value=source_text,
        numeric_value=numeric,
        canonical_value=canonical_numeric,
        canonical_unit=canonical_unit,
        status_code=status,
        period_source_code=period,
        period_label=period,
        dimensions=dimensions,
        source_cell_key=f"{sheet_name}:{period}:{row_index}:{column}",
    )


def _interpret_static_cell(cell: Cell) -> tuple[str, float | None, str | None]:
    if cell.number is not None and not is_status_token(cell.text, letter_flags=False):
        return format_excel_number(cell.number), float(cell.number), None
    text = cell.text.strip()
    if is_status_token(text, letter_flags=False):
        return text, None, text.casefold()
    return text, _text_number(text), None


_COMMA_DECIMAL = re.compile(r"[+-]?\d+,\d+")
_SPACE_THOUSANDS = re.compile(r"[+-]?\d{1,3}( \d{3})+(,\d+)?")
_DOT_THOUSANDS_WITH_DECIMAL = re.compile(r"[+-]?\d{1,3}(\.\d{3})+,\d+")
_DOT_THOUSANDS_MULTI = re.compile(r"[+-]?\d{1,3}(\.\d{3}){2,}")
_DOT_DECIMAL = re.compile(r"[+-]?\d+\.\d+")
_PLAIN_INTEGER = re.compile(r"[+-]?\d+")
_MARKER_SUFFIX = re.compile(r"\s*(\*+\)?|r)$")


def _text_number(text: str) -> float | None:
    """Read a number that a workbook stores as text, in Indonesian notation.

    Comma is the decimal mark, and space or dot group thousands. A single dot with exactly three digits
    after it (``1.234``) could be either notation, so it stays text. A trailing footnote or revision
    marker (``*)``, ``*``, ``r``) is ignored; the stored source text keeps it. Anything else, such as
    ``2,36 1`` with a footnote digit, stays text.
    """
    token = _MARKER_SUFFIX.sub("", text.strip())
    if _DOT_THOUSANDS_MULTI.fullmatch(token):
        return float(token.replace(".", ""))
    if _PLAIN_INTEGER.fullmatch(token) or _COMMA_DECIMAL.fullmatch(token):
        return float(token.replace(",", "."))
    if _SPACE_THOUSANDS.fullmatch(token):
        return float(token.replace(" ", "").replace(",", "."))
    if _DOT_THOUSANDS_WITH_DECIMAL.fullmatch(token):
        return float(token.replace(".", "").replace(",", "."))
    if _DOT_DECIMAL.fullmatch(token) and not re.fullmatch(r"[+-]?[1-9]\d{0,2}\.\d{3}", token):
        return float(token)
    return None


def _row_dimensions(
    row: list[Cell],
    stub_indexes: list[int],
    labels: dict[int, str],
    *,
    carried: dict[int, str],
    groups: dict[int, str],
    caption: str | None,
) -> list[DimensionDraft]:
    """Row members from the stub cells.

    ``carried`` holds the last label of each outer stub, for rows that leave a merged outer cell blank.
    ``groups`` holds the current upper-case group row of stubs whose labels repeat (a ``kelompok`` axis), and
    ``caption`` the last label-only row above the data (a ``bagian baris`` axis).
    """
    drafts: list[DimensionDraft] = []
    for index in stub_indexes:
        label = _stub_text(row, index) or carried.get(index, "")
        if not label:
            continue
        drafts.append(dimension_draft(labels.get(index) or "baris", label))
    group_labels = [group for index, group in groups.items() if group != _stub_text(row, index)]
    for level, group in enumerate(group_labels, start=1):
        drafts.append(dimension_draft("kelompok" if level == 1 else f"kelompok {level}", group))
    if caption:
        drafts.append(dimension_draft("bagian baris", caption))
    return drafts


def dimension_draft(axis_label: str, member_label: str) -> DimensionDraft:
    """Build one member on the axis named ``axis_label``."""
    return DimensionDraft(
        source_code=stable_code(axis_label),
        original_label=axis_label,
        member_source_code=stable_code(member_label),
        member_label=member_label,
        dimension_type=classify_dimension_type(axis_label),
        canonical_code=canonical_member(member_label),
    )


def _stub_text(row: list[Cell], index: int) -> str:
    return row[index].text.strip() if index < len(row) else ""


def _is_group_label(label: str) -> bool:
    """An upper-case row label such as ``PADI-PADIAN`` that heads the rows under it."""
    return any(character.isalpha() for character in label) and label == label.upper()


def _has_repeated_labels(rows: list[list[Cell]], index: int) -> bool:
    labels = [normalize_label(_stub_text(row, index)) for row in rows if _stub_text(row, index)]
    return len(labels) != len(set(labels))


def _is_caption_row(row: list[Cell], first_year: int) -> bool:
    """A data-area row holding only a stub label that ends with a colon, such as ``Migrasi Masuk :``."""
    filled = [(column, cell) for column, cell in enumerate(row) if not cell.blank]
    if len(filled) != 1:
        return False
    column, cell = filled[0]
    return column < first_year and cell.number is None and cell.text.strip().endswith(":")


def _inner_stubs(year_row: list[Cell], rows: list[list[Cell]], first_year: int) -> list[int]:
    """Label columns inside the year area that restate the row labels for the years to their right.

    The year row names them (``Tambahan Komoditi Makanan``) and their data cells are text.
    """
    inner: list[int] = []
    for column in range(first_year + 1, len(year_row)):
        cell = year_row[column]
        if cell.blank or _period_token(cell) is not None or cell.number is not None:
            continue
        texts = sum(1 for row in rows if _stub_text(row, column) and row[column].number is None)
        values = sum(1 for row in rows if column < len(row) and row[column].number is not None)
        if texts >= 2 and texts > values:
            inner.append(column)
    return inner


def _stub_segments(
    dimension_stubs: list[int],
    inner_stubs: list[int],
    year_by_column: dict[int, str],
) -> list[tuple[list[int], list[int]]]:
    """Pair each value column with the stub columns that label its rows: the nearest inner stub on its left,
    or the leading stubs."""
    segments: dict[int, tuple[list[int], list[int]]] = {-1: (dimension_stubs, [])}
    for inner in inner_stubs:
        segments[inner] = ([inner], [])
    for column in year_by_column:
        owner = max((inner for inner in inner_stubs if inner < column), default=-1)
        segments[owner][1].append(column)
    return [segment for segment in segments.values() if segment[1]]


def _fill_headers(headers: list[tuple[int, list[Cell]]], first_year: int) -> list[list[Cell]]:
    """Carry a header label right across its merged span.

    A blank cell takes the label on its left only when every higher header row is also blank there,
    so a lower label never crosses into a column that a higher row starts.
    """
    width = max((len(row) for _index, row in headers), default=0)
    padded = [_pad(row, width) for _index, row in headers]
    filled: list[list[Cell]] = []
    for level, row in enumerate(padded):
        out = list(row)
        for column in range(first_year + 1, width):
            if not row[column].blank:
                continue
            if any(not higher[column].blank for higher in padded[:level]):
                continue
            out[column] = out[column - 1]
        filled.append(out)
    return filled


def _column_dimensions(
    headers: list[tuple[tuple[int, list[Cell]], list[Cell]]],
    column: int,
    first_year: int,
    label_rows: set[tuple[int, int]],
    taken: set[str],
) -> list[DimensionDraft]:
    """Name each header level's axis from a label in its stub area. Unnamed levels are ``kolom``.

    ``headers`` pairs each (row index, original row) with its filled copy, top to bottom.
    """
    drafts: list[DimensionDraft] = []
    used = set(taken)
    for level, ((row_index, row), filled_row) in enumerate(headers, start=1):
        cell = filled_row[column]
        if cell.blank:
            continue
        member_label = cell.text.strip()
        if _period_token(cell) is not None:
            continue
        axis_label = ""
        for stub in range(first_year - 1, -1, -1):
            if (row_index, stub) in label_rows:
                continue
            if stub < len(row) and row[stub].text.strip():
                axis_label = row[stub].text.strip()
                break
        if normalize_label(axis_label) == "tahun":
            continue
        dimension_label = axis_label or "kolom"
        if stable_code(dimension_label) in used:
            dimension_label = f"{dimension_label} {level}"
        used.add(stable_code(dimension_label))
        drafts.append(
            DimensionDraft(
                source_code=stable_code(dimension_label),
                original_label=dimension_label,
                member_source_code=stable_code(member_label),
                member_label=member_label,
                dimension_type=classify_dimension_type(dimension_label),
                canonical_code=canonical_member(member_label),
            )
        )
    return drafts


def _stub_labels(
    headers: list[tuple[int, list[Cell]]],
    stub_indexes: list[int],
    first_year: int,
) -> tuple[dict[int, str], set[tuple[int, int]]]:
    """Return each stub column's label and the (row, column) cells those labels came from.

    ``headers`` runs top to bottom through the rows above the years, the year row, and the rows below.
    The topmost label in the stub column wins. ``Tahun`` names the year axis, not a stub. A stub without
    its own label takes the label over a row-number column on its left, as in a merged ``Provinsi`` header.
    """
    labels: dict[int, tuple[str, tuple[int, int]]] = {}
    for index in range(first_year):
        for row_index, header in headers:
            text = _stub_text(header, index)
            if text and normalize_label(text) != "tahun":
                labels[index] = (text, (row_index, index))
                break
    resolved: dict[int, str] = {}
    sources: set[tuple[int, int]] = set()
    unnamed = 0
    for index in stub_indexes:
        found = labels.get(index)
        if found is None and index > 0 and index - 1 not in stub_indexes:
            found = labels.get(index - 1)
        if found is None:
            unnamed += 1
            resolved[index] = "baris" if unnamed == 1 else f"baris {unnamed}"
            continue
        resolved[index] = found[0]
        sources.add(found[1])
    return resolved, sources


def _stub_indexes(data_rows: list[list[Cell]], year_row: list[Cell], first_year: int) -> list[int]:
    """Stub columns left of the years. A column of bare numbers beside a text stub is a row number."""
    indexes: list[int] = []
    numbered: list[int] = []
    for index in range(first_year):
        label = year_row[index].text if index < len(year_row) else ""
        if _is_index_header(label):
            continue
        cells = [row[index] for row in data_rows if _stub_text(row, index) and not _is_note_row(row)]
        if not cells:
            continue
        if all(cell.number is not None for cell in cells):
            numbered.append(index)
        indexes.append(index)
    if len(numbered) < len(indexes):
        indexes = [index for index in indexes if index not in numbered]
    return indexes


def _section_stub(paths: list[str], data_rows: list[tuple[int, list[Cell]]]) -> int | None:
    for index, path in enumerate(paths):
        if _is_index_header(path):
            continue
        folded = normalize_label(path)
        if "partai politik" in folded or folded == "provinsi":
            return index
    best_index = None
    best_score = 0
    for index, path in enumerate(paths):
        if _is_index_header(path):
            continue
        score = 0
        for _row_index, row in data_rows:
            if index < len(row) and row[index].text.strip() and row[index].number is None:
                score += 1
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def _section_title_year(row: list[Cell]) -> int | None:
    populated = [cell for cell in row if not cell.blank]
    if len(populated) != 1 or populated[0].number is not None:
        return None
    years = year_in_text(populated[0].text)
    if len(years) == 1:
        return years[0]
    return None


def _is_data_row(row: list[Cell]) -> bool:
    return any(cell.number is not None for cell in row)


def _is_note_row(row: list[Cell]) -> bool:
    text = row[0].text.strip() if row else ""
    return normalize_label(text).startswith("sumber")


def _is_indicator(cell: Cell) -> bool:
    return "indikator" in normalize_label(cell.text)


def _is_index_header(label: str) -> bool:
    folded = normalize_label(label)
    if folded in {"no", "no.", "nomor"}:
        return True
    return folded.startswith("no") and "perolehan" in folded


def _is_code_header(label: str) -> bool:
    folded = normalize_label(label)
    return (folded.startswith("no. urut") or folded.startswith("no urut")) and "perolehan" not in folded


def _code_dimensions(paths: list[str], occupied: set[int], row: list[Cell]) -> tuple[DimensionDraft, ...]:
    drafts: list[DimensionDraft] = []
    for column, path in enumerate(paths):
        if column not in occupied or not _is_code_header(path):
            continue
        label = row[column].text.strip() if column < len(row) else ""
        if not label:
            continue
        drafts.append(
            DimensionDraft(
                source_code=stable_code(path),
                original_label=path,
                member_source_code=stable_code(label),
                member_label=label,
                dimension_type="generic_category",
                canonical_code=None,
            )
        )
    return tuple(drafts)


def _header_path(rows: list[list[Cell]], column: int) -> str:
    parts: list[str] = []
    for row in rows:
        if column < len(row) and row[column].text.strip():
            parts.append(row[column].text.strip())
    return " / ".join(parts)


def _forward_fill(row: list[Cell]) -> list[Cell]:
    filled: list[Cell] = []
    last = Cell("", None)
    for cell in row:
        if cell.blank:
            filled.append(last if not last.blank else cell)
            continue
        last = cell
        filled.append(cell)
    return filled


def _pad(row: list[Cell], width: int) -> list[Cell]:
    if len(row) >= width:
        return row
    return [*row, *([Cell("", None)] * (width - len(row)))]


def _sheet_title(grid: list[list[Cell]]) -> str:
    for row in grid[:3]:
        for cell in row:
            if cell.text.strip():
                return cell.text.strip()
    return ""


_ENGLISH_SHEET = re.compile(r"(?:^|.*[_\s-])(?:en|eng|english)", re.IGNORECASE)


def _select_sheets(sheets: list[tuple[str, list[list[Cell]]]]) -> list[tuple[str, list[list[Cell]]]]:
    """Keep the Indonesian sheets. Blank sheets and scratch sheets without a single word are dropped."""
    sheets = [sheet for sheet in sheets if any(re.search(r"[^\W\d_]", cell.text) for row in sheet[1] for cell in row)]
    indonesian = [sheet for sheet in sheets if sheet[0].casefold().startswith("indo")]
    if indonesian:
        return indonesian
    return [sheet for sheet in sheets if not _ENGLISH_SHEET.fullmatch(sheet[0].strip())]


def load_sheets(path: Path) -> list[tuple[str, list[list[Cell]]]]:
    """Return every sheet of a ``.xls`` or ``.xlsx`` workbook as a grid of cells."""
    blob = path.read_bytes()[:2]
    if path.suffix.lower() == ".xlsx" or blob == b"PK":
        return _load_xlsx(path)
    return _load_xls(path)


def _load_xls(path: Path) -> list[tuple[str, list[list[Cell]]]]:
    import xlrd

    book = _open_xls(path)
    sheets: list[tuple[str, list[list[Cell]]]] = []
    for sheet in book.sheets():
        grid: list[list[Cell]] = []
        for row_index in range(sheet.nrows):
            row: list[Cell] = []
            for column in range(sheet.ncols):
                cell = sheet.cell(row_index, column)
                if cell.ctype == xlrd.XL_CELL_NUMBER:
                    number = float(cell.value)
                    row.append(Cell(format_excel_number(number), number))
                elif cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                    row.append(Cell("", None))
                else:
                    row.append(_text_cell(str(cell.value)))
            grid.append(row)
        sheets.append((sheet.name, grid))
    return sheets


def _text_cell(value: str) -> Cell:
    """A text cell. A stray quote or backtick alone in a cell is a typing slip, so it is blank."""
    text = value.strip()
    return Cell("", None) if text in {"`", "'", "´", '"'} else Cell(text, None)


def _open_xls(path: Path) -> Any:
    """Open a BIFF workbook, skipping defined names whose formulas xlrd cannot read.

    Some BPS workbooks store chart names with tokens xlrd rejects. Cell values do not use those names.
    """
    import xlrd
    from xlrd.formula import FormulaError

    original = xlrd.book.evaluate_name_formula

    def _skip_unreadable_name(*args: object, **kwargs: object) -> None:
        try:
            original(*args, **kwargs)
        except FormulaError:
            return

    xlrd.book.evaluate_name_formula = _skip_unreadable_name
    try:
        return xlrd.open_workbook(str(path), formatting_info=False)
    finally:
        xlrd.book.evaluate_name_formula = original


def _load_xlsx(path: Path) -> list[tuple[str, list[list[Cell]]]]:
    import openpyxl

    book = openpyxl.load_workbook(path, data_only=True)
    sheets: list[tuple[str, list[list[Cell]]]] = []
    for sheet in book.worksheets:
        grid: list[list[Cell]] = []
        for values in sheet.iter_rows(values_only=True):
            row: list[Cell] = []
            for value in values:
                if isinstance(value, bool) or value is None:
                    row.append(Cell("", None))
                elif isinstance(value, int | float):
                    number = float(value)
                    row.append(Cell(format_excel_number(number), number))
                else:
                    row.append(_text_cell(str(value)))
            grid.append(row)
        sheets.append((sheet.title, grid))
    book.close()
    return sheets
