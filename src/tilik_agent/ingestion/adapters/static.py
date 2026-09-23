"""Parse official BPS static workbooks into observations."""

from __future__ import annotations

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
    sheets = _load_sheets(path)
    selected = _select_sheets(sheets)
    if not selected:
        return ParseResult(needs_review=True, review_reason="Workbook contained no sheets")
    parsed: list[ParseResult] = []
    for name, grid in selected:
        result = _parse_sheet(name, grid, fallback_title=fallback_title)
        if result.needs_review:
            return result
        parsed.append(result)
    observations = [draft for result in parsed for draft in result.observations]
    notes = "\n".join(result.notes for result in parsed if result.notes)
    duplicate = duplicate_reason(observations)
    if duplicate:
        return ParseResult(needs_review=True, review_reason=duplicate, notes=notes or None)
    if not observations:
        return ParseResult(needs_review=True, review_reason="Workbook produced no observations")
    return ParseResult(observations=observations, notes=notes or None)


def _parse_sheet(sheet_name: str, grid: list[list[Cell]], *, fallback_title: str) -> ParseResult:
    title = _sheet_title(grid) or fallback_title
    year_layout = _parse_year_columns(sheet_name, grid, title)
    if year_layout is not None:
        return year_layout
    section_layout = _parse_year_sections(sheet_name, grid, title)
    if section_layout is not None:
        return section_layout
    return ParseResult(
        needs_review=True,
        review_reason=f"Sheet {sheet_name} has no year columns or single-year sections",
    )


def _parse_year_columns(sheet_name: str, grid: list[list[Cell]], title: str) -> ParseResult | None:
    year_headers = _year_header_indexes(grid)
    if not year_headers:
        return None
    observations: list[ObservationDraft] = []
    notes: list[str] = []
    for position, header_index in enumerate(year_headers):
        year_row = grid[header_index]
        year_columns = [
            index
            for index, cell in enumerate(year_row)
            if is_year_token(cell.text, cell.number) is not None
        ]
        first_year = year_columns[0]
        header_rows = _headers_above(grid, header_index, first_year)
        data_end = _next_block_start(grid, year_headers, position, first_year)
        data_rows = list(enumerate(grid[header_index + 1 : data_end], start=header_index + 1))
        filled_headers = [(index, _forward_fill(row)) for index, row in header_rows]
        stub_indexes = _stub_indexes([row for _index, row in data_rows], year_row, first_year)
        indicator_index = next((index for index in stub_indexes if _is_indicator(year_row[index])), None)
        dimension_stubs = [index for index in stub_indexes if index != indicator_index]
        stub_labels = _stub_labels(header_rows, year_row, dimension_stubs)
        for row_index, row in data_rows:
            if _is_note_row(row):
                notes.append(row[0].text)
                continue
            if _is_year_header(row):
                continue
            measure_label = row[indicator_index].text.strip() if indicator_index is not None else title
            if indicator_index is None and not any(row[index].text.strip() for index in dimension_stubs):
                continue
            if indicator_index is not None and not measure_label:
                continue
            row_dimensions = _row_dimensions(row, dimension_stubs, stub_labels)
            for column in year_columns:
                cell = row[column] if column < len(row) else Cell("", None)
                if cell.blank:
                    continue
                year = is_year_token(year_row[column].text, year_row[column].number)
                if year is None:
                    continue
                column_dimensions = _column_dimensions(filled_headers, column, first_year)
                observations.append(
                    _value_draft(
                        sheet_name=sheet_name,
                        row_index=row_index,
                        column=column,
                        measure_label=measure_label,
                        period=str(year),
                        cell=cell,
                        dimensions=tuple(row_dimensions + column_dimensions),
                        unit_label=measure_label,
                    )
                )
    if not observations:
        return ParseResult(needs_review=True, review_reason=f"Sheet {sheet_name} year columns had no values")
    return ParseResult(observations=observations, notes="\n".join(notes) or None)


def _year_header_indexes(grid: list[list[Cell]]) -> list[int]:
    return [index for index, row in enumerate(grid) if _is_year_header(row)]


def _is_year_header(row: list[Cell]) -> bool:
    year_count = sum(is_year_token(cell.text, cell.number) is not None for cell in row)
    nonempty = sum(not cell.blank for cell in row)
    return year_count >= 3 and year_count * 2 >= nonempty


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
            column
            for column in range(width)
            if any(column < len(row) and not row[column].blank for row in header_rows)
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
                    _value_draft(
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


def _value_draft(
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
    return text, None, None


def _row_dimensions(row: list[Cell], stub_indexes: list[int], labels: dict[int, str]) -> list[DimensionDraft]:
    drafts: list[DimensionDraft] = []
    for index in stub_indexes:
        label = row[index].text.strip() if index < len(row) else ""
        if not label:
            continue
        header_label = labels.get(index) or "baris"
        drafts.append(
            DimensionDraft(
                source_code=stable_code(header_label or "baris"),
                original_label=header_label or "baris",
                member_source_code=stable_code(label),
                member_label=label,
                dimension_type=classify_dimension_type(header_label or "baris"),
                canonical_code=canonical_member(label),
            )
        )
    return drafts


def _column_dimensions(
    headers: list[tuple[int, list[Cell]]],
    column: int,
    first_year: int,
) -> list[DimensionDraft]:
    drafts: list[DimensionDraft] = []
    for _index, row in headers:
        if column >= len(row) or row[column].blank:
            continue
        member_label = row[column].text.strip()
        if is_year_token(member_label, row[column].number) is not None:
            continue
        stub_label = ""
        for stub in range(first_year - 1, -1, -1):
            if stub < len(row) and row[stub].text.strip():
                stub_label = row[stub].text.strip()
                break
        dimension_label = stub_label or "kolom"
        if normalize_label(dimension_label) == "tahun":
            continue
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
    year_row: list[Cell],
    stub_indexes: list[int],
) -> dict[int, str]:
    labels: dict[int, str] = {}
    for index in stub_indexes:
        label = ""
        for _row_index, header in headers:
            if index < len(header) and header[index].text.strip():
                label = header[index].text.strip()
                break
        if not label and index < len(year_row):
            label = year_row[index].text.strip()
        labels[index] = label or "baris"
    return labels


def _stub_indexes(data_rows: list[list[Cell]], year_row: list[Cell], first_year: int) -> list[int]:
    indexes: list[int] = []
    for index in range(first_year):
        label = year_row[index].text if index < len(year_row) else ""
        if _is_index_header(label):
            continue
        has_data = any(index < len(row) and row[index].text.strip() and not _is_note_row(row) for row in data_rows)
        if has_data:
            indexes.append(index)
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


def _select_sheets(sheets: list[tuple[str, list[list[Cell]]]]) -> list[tuple[str, list[list[Cell]]]]:
    indonesian = [sheet for sheet in sheets if sheet[0].casefold().startswith("indo")]
    if indonesian:
        return indonesian
    return [sheet for sheet in sheets if sheet[0].casefold() not in {"eng", "en", "english"}]


def _load_sheets(path: Path) -> list[tuple[str, list[list[Cell]]]]:
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
                    row.append(Cell(str(cell.value).strip(), None))
            grid.append(row)
        sheets.append((sheet.name, grid))
    return sheets


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
                    row.append(Cell(str(value).strip(), None))
            grid.append(row)
        sheets.append((sheet.title, grid))
    book.close()
    return sheets
