"""Tests for static workbook header layouts found in the BPS catalog."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import openpyxl
import pytest

from tilik_agent.ingestion.adapters.static import (
    Cell,
    _period_token,
    _text_number,
    _year_header_indexes,
    parse_workbook,
)
from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft
from tilik_agent.ingestion.parsing import duplicate_reason

Grid = list[list[str | float | None]]
WorkbookFactory = Callable[..., Path]


@pytest.fixture
def make_workbook(tmp_path: Path) -> WorkbookFactory:
    """Write one or more named sheets to an xlsx file and return its path."""

    def _make(**sheets: Grid) -> Path:
        book = openpyxl.Workbook()
        book.remove(book.active)
        for name, grid in sheets.items():
            sheet = book.create_sheet(name)
            for row in grid:
                sheet.append(row)
        path = tmp_path / "workbook.xlsx"
        book.save(path)
        return path

    return _make


def _axes(draft: ObservationDraft) -> dict[str, str]:
    return {dimension.original_label: dimension.member_label for dimension in draft.dimensions}


def _by_cell(drafts: list[ObservationDraft]) -> dict[tuple[str, str, str], ObservationDraft]:
    """Index drafts by (period, first row member, last column member)."""
    return {
        (draft.period_label, draft.dimensions[0].member_label, draft.dimensions[-1].member_label): draft
        for draft in drafts
    }


def test_group_label_beside_the_stub_header_is_its_own_axis(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a sheet whose top header row holds ``Provinsi`` over the region column and ``Makanan`` /
    ``Bukan Makanan`` over groups of year columns.

    Expected:
    ``Provinsi`` names the row axis only. The groups land on a separate column axis, so no cell has
    two members on one axis.
    """
    path = make_workbook(
        indo=[
            ["Rata-rata Pengeluaran"],
            [],
            ["Provinsi", "Makanan", None, "Bukan Makanan", None],
            [None, 2024, 2025, 2024, 2025],
            ["Aceh", 10, 11, 20, 21],
            ["Bali", 12, 13, 22, 23],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert len(result.observations) == 8
    assert duplicate_reason(result.observations) is None
    cell = _by_cell(result.observations)[("2025", "Bali", "Bukan Makanan")]
    assert cell.numeric_value == 23
    assert _axes(cell) == {"Provinsi": "Bali", "kolom": "Bukan Makanan"}


def test_sparse_years_with_categories_below_and_a_caption(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a block captioned ``Daerah Perkotaan`` whose year row names each year once, with the
    ``Jenis Kelamin`` categories in the row below it.

    Expected:
    Every category column is read under its year, the caption is a ``bagian`` axis, and the region
    axis keeps the ``Provinsi`` label.
    """
    path = make_workbook(
        indo=[
            ["Persentase Rumah Tangga"],
            [],
            ["Daerah Perkotaan"],
            ["Provinsi", "Tahun", 2009, None, 2010, None, 2011, None],
            [None, "Jenis Kelamin", "Laki-laki", "Perempuan", "Laki-laki", "Perempuan", "Laki-laki", "Perempuan"],
            ["Aceh", None, 83.08, 16.92, 83.77, 16.23, 82.44, 17.56],
            ["Bali", None, 80.1, 19.9, 80.2, 19.8, 80.3, 19.7],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert len(result.observations) == 12
    cell = _by_cell(result.observations)[("2010", "Aceh", "Daerah Perkotaan")]
    assert _axes(cell) == {"Provinsi": "Aceh", "Jenis Kelamin": "Perempuan", "bagian": "Daerah Perkotaan"}
    assert cell.numeric_value == 16.23


def test_stacked_generic_headers_get_distinct_axes(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given two unnamed header rows (sex over treatment type) above the year row.

    Expected:
    Each header row becomes its own column axis, ``kolom`` and ``kolom 2``.
    """
    path = make_workbook(
        indo=[
            ["Persentase Penduduk"],
            [],
            ["Provinsi", "Laki-laki", None, None],
            [None, "Obat Modern", None, None],
            [None, 2009, 2010, 2011],
            ["Aceh", 89.81, 91.6, 88.82],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert _axes(result.observations[0]) == {"Provinsi": "Aceh", "kolom": "Laki-laki", "kolom 2": "Obat Modern"}


def test_stub_header_on_the_category_row_below_the_years(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given year blocks side by side whose category row below the years starts with ``Provinsi``,
    followed by a ``(1) (2) (3)`` column-number row.

    Expected:
    The category row is a header, not data, and the column-number row is skipped.
    """
    path = make_workbook(
        IND=[
            ["Jumlah Rumah Sakit"],
            [],
            [None, 2020, None, 2019, 2018],
            ["Provinsi", "Kelas A", "Kelas B", "Jumlah", "Jumlah"],
            ["(1)", "(2)", "(3)", "(4)", "(5)"],
            ["Aceh", 2, 10, 70, 68],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    cells = _by_cell(result.observations)
    assert set(cells) == {
        ("2020", "Aceh", "Kelas A"),
        ("2020", "Aceh", "Kelas B"),
        ("2019", "Aceh", "Jumlah"),
        ("2018", "Aceh", "Jumlah"),
    }
    assert _axes(cells[("2020", "Aceh", "Kelas B")]) == {"Provinsi": "Aceh", "kolom": "Kelas B"}


def test_empty_extra_sheets_are_ignored(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a workbook with one data sheet and two empty default sheets.

    Expected:
    The data sheet is parsed and the empty sheets do not send the workbook to review.
    """
    path = make_workbook(
        Sheet1=[["Judul"], [], ["Provinsi", 2023, 2024, 2025], ["Aceh", 1, 2, 3]],
        Sheet2=[],
        Sheet3=[],
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert len(result.observations) == 3


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2,28", 2.28),
        ("100,00", 100.0),
        ("451 952", 451952.0),
        ("1 183,0", 1183.0),
        ("1.039.606,25", 1039606.25),
        ("96.214.691", 96214691.0),
        ("0.029", 0.029),
        ("2.36", 2.36),
        ("11.44*)", 11.44),
        ("73038r", 73038.0),
        ("94073 r", 94073.0),
        ("1.234", None),
        ("2,36 1", None),
        ("0,68,", None),
        ("02.02'37''", None),
        ("Kelas A", None),
    ],
)
def test_text_number(text: str, expected: float | None) -> None:
    """
    Condition:
    Given numbers a workbook stores as text in Indonesian notation, and text that is not a number.

    Expected:
    Unambiguous numbers are read. ``1.234`` could be either notation and stays text, as do typos,
    footnote digits, durations, and labels.
    """
    assert _text_number(text) == expected


def test_text_numbers_keep_the_printed_source_value(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given value cells stored as text, such as ``2,28`` and ``11.44*)``.

    Expected:
    The number is parsed and the source value stays exactly as printed.
    """
    path = make_workbook(indo=[["Judul"], [], ["Provinsi", 2023, 2024, 2025], ["Aceh", "2,28", "11.44*)", "-"]])
    drafts = {draft.period_label: draft for draft in parse_workbook(path, fallback_title="x").observations}
    assert (drafts["2023"].numeric_value, drafts["2023"].source_value) == (2.28, "2,28")
    assert (drafts["2024"].numeric_value, drafts["2024"].source_value) == (11.44, "11.44*)")
    assert (drafts["2025"].numeric_value, drafts["2025"].status_code) == (None, "-")


@pytest.mark.parametrize(
    "text,number,expected",
    [
        ("2020", 2020.0, "2020"),
        ("SP1971", None, "SP1971"),
        ("LF SP2020", None, "LF SP2020"),
        ("1971-1980", None, "1971-1980"),
        ("2010 - 2017", None, "2010-2017"),
        ("2010-20151", None, "2010-2015"),
        ("2013/2014", None, "2013/2014"),
        ("2008 2", None, "2008"),
        ("2000 1)", None, "2000"),
        ("2011*", None, "2011"),
        ("2013 r", None, "2013"),
        ("Sept  2012", None, "September 2012"),
        ("15-19", None, None),
        ("2008 Februari", None, "Februari 2008"),
        ("2008 Kelas", None, None),
        ("Kelas A", None, None),
    ],
)
def test_period_token(text: str, number: float | None, expected: str | None) -> None:
    """
    Condition:
    Given year-row headers as BPS prints them: years, census waves, ranges, academic years, month-years,
    and years with a footnote or revision marker.

    Expected:
    Each period is returned as published, without the marker. Age bands, labels, and a year followed by
    a month are not periods.
    """
    assert _period_token(Cell(text, number)) == expected


def test_census_waves_are_period_columns(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given an English twin sheet, a scratch sheet of bare numbers, and a sheet whose columns are census
    waves ``SP1971`` to ``LF SP2020``.

    Expected:
    Only the Indonesian sheet is read, with one period per wave.
    """
    waves = ["SP1971", "SP1980", "LF SP2020"]
    path = make_workbook(
        TFR_Prov_id=[["Angka Kelahiran Total"], [], ["Provinsi", *waves], ["ACEH", 6.27, 5.24, 2.42]],
        TFR_Prov_en=[["Total Fertility Rate"], [], ["Province", *waves], ["ACEH", 6.27, 5.24, 2.42]],
        Sheet1=[[None, 52, 329], [None, "069", 12]],
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert [draft.period_label for draft in result.observations] == ["SP1971", "SP1980", "LF SP2020"]
    assert {draft.dimensions[0].original_label for draft in result.observations} == {"Provinsi"}


def test_marked_year_beside_the_same_plain_year_keeps_its_marker(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a year row with both ``2011`` and ``2011*`` (a revised series), and a lone ``2012*``.

    Expected:
    Both 2011 columns survive, the revised one under period ``2011*``. The lone marked year is ``2012``.
    """
    path = make_workbook(indo=[["Judul"], [], ["Provinsi", 2010, 2011, "2011*", "2012*"], ["Aceh", 1, 2, 3, 4]])
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert {draft.period_label: draft.numeric_value for draft in result.observations} == {
        "2010": 1,
        "2011": 2,
        "2011*": 3,
        "2012": 4,
    }


def test_unreadable_period_header_is_sent_to_review(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a year row whose last header mentions a year but is not a period.

    Expected:
    The sheet is kept for review instead of silently dropping that column.
    """
    path = make_workbook(indo=[["Judul"], [], ["Provinsi", 2010, 2011, 2012, "2013 sementara"], ["Aceh", 1, 2, 3, 4]])
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is True
    assert result.review_reason == "Sheet indo period header '2013 sementara' is not a readable period"


def test_sparse_year_row_under_a_title(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a year row with two years under a title, categories below it, and a column-number row that
    Excel stored as ``-1 -2 -3``.

    Expected:
    Both years are read with their categories, and the column-number row is skipped.
    """
    path = make_workbook(
        indo=[
            ["Perolehan Suara"],
            [],
            ["Partai Politik", 2019, None, 2024, None],
            [None, "Suara", "Kursi", "Suara", "Kursi"],
            [-1, -2, -3, -4, -5],
            ["Partai A", 100, 10, 120, 12],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    cells = _by_cell(result.observations)
    assert set(cells) == {
        ("2019", "Partai A", "Suara"),
        ("2019", "Partai A", "Kursi"),
        ("2024", "Partai A", "Suara"),
        ("2024", "Partai A", "Kursi"),
    }
    assert cells[("2024", "Partai A", "Kursi")].numeric_value == 12


@pytest.mark.parametrize(
    "grid",
    [
        [["Judul"], [], ["Provinsi", "Tahun Berdiri"], ["Aceh", 1990], ["Bali", 2001]],
        [["Judul"], [], ["Provinsi", "Jumlah", "Tahun"], ["Aceh", 10, 1990], ["Bali", None, 2001]],
    ],
)
def test_value_that_looks_like_a_year_is_not_a_year_row(grid: list[list[str | float | None]]) -> None:
    """
    Condition:
    Given data rows whose only value is a year-like number, under a column header or another data row.

    Expected:
    No row is taken as a year header.
    """
    cells = [
        [Cell("" if value is None else str(value), value if isinstance(value, int) else None) for value in row]
        for row in grid
    ]
    assert _year_header_indexes(cells) == []


def test_year_block_with_its_own_stub_column_and_repeated_items(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a commodity table whose later years have their own commodity column, where ``Lainnya`` repeats
    under two upper-case groups and ``Tuna`` exists only in the later commodity list.

    Expected:
    Each year block takes its rows from its own commodity column, and repeated items are told apart by a
    ``kelompok`` axis.
    """
    path = make_workbook(
        indo=[
            ["Rata-rata Konsumsi"],
            [],
            ["Komoditi", 2018, 2019, 2020, "Tambahan Komoditi", 2022, 2023, 2024],
            ["PADI-PADIAN", 1, 1, 1, "PADI-PADIAN", 2, 2, 2],
            ["Lainnya", 3, 3, 3, "Lainnya", 4, 4, 4],
            ["IKAN", 5, 5, 5, "IKAN", 6, 6, 6],
            [None, None, None, None, "Tuna", 7, 7, 7],
            ["Lainnya", 8, 8, 8, "Lainnya", 9, 9, 9],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    assert duplicate_reason(result.observations) is None
    cells = {(draft.period_label, tuple(_axes(draft).items())): draft.numeric_value for draft in result.observations}
    assert cells[("2019", (("Komoditi", "Lainnya"), ("kelompok", "IKAN")))] == 8
    assert cells[("2022", (("Komoditi", "Lainnya"), ("kelompok", "PADI-PADIAN")))] == 4
    assert cells[("2023", (("Komoditi", "Tuna"), ("kelompok", "IKAN")))] == 7
    assert not any(period == "2018" and ("Komoditi", "Tuna") in axes for period, axes in cells)


def test_row_numbers_and_colon_captions(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a numbered province list split by caption rows ``Migrasi Masuk :`` and ``Migrasi Keluar :``,
    with ``Provinsi`` written over the row-number column.

    Expected:
    The row numbers are dropped, the provinces take the ``Provinsi`` label, and each caption becomes a
    ``bagian baris`` member.
    """
    path = make_workbook(
        indo=[
            ["Migrasi Risen"],
            [],
            ["Provinsi", None, 1980, 1985, 1990],
            ["Migrasi Masuk :"],
            [1, "Aceh", 10, 11, 12],
            [2, "Bali", 13, 14, 15],
            ["Migrasi Keluar :"],
            [1, "Aceh", 20, 21, 22],
            [2, "Bali", 23, 24, 25],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    cells = {(draft.period_label, tuple(_axes(draft).items())): draft.numeric_value for draft in result.observations}
    assert cells[("1985", (("Provinsi", "Bali"), ("bagian baris", "Migrasi Keluar")))] == 24
    assert len(cells) == 12


@pytest.mark.parametrize(
    "caption_rows,expected_axes",
    [
        (
            [["Kelompok Umur", 2013, 2014, 2015], ["Laki-laki"], ["7-12", 1, 2, 3], ["Perempuan"], ["7-12", 4, 5, 6]],
            {"Kelompok Umur": "7-12", "bagian baris": "Perempuan"},
        ),
        (
            [["Kelompok Umur", 2013, 2014, 2015], ["PEDESAAN"], ["7-12", 1, 2, 3], ["PERKOTAAN"], ["7-12", 4, 5, 6]],
            {"Kelompok Umur": "7-12", "kelompok": "PERKOTAAN"},
        ),
    ],
)
def test_label_only_rows_split_repeated_row_labels(
    make_workbook: WorkbookFactory,
    caption_rows: list[list[str | float | None]],
    expected_axes: dict[str, str],
) -> None:
    """
    Condition:
    Given row labels that repeat under label-only rows, written in mixed case or in upper case.

    Expected:
    A mixed-case label-only row is a ``bagian baris`` member and an upper-case one a ``kelompok`` member,
    so the repeated rows stay distinct.
    """
    path = make_workbook(indo=[["Persentase Penduduk"], [], *caption_rows])
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    last = next(draft for draft in result.observations if draft.numeric_value == 6)
    assert _axes(last) == expected_axes


def test_outer_stub_is_carried_down_merged_cells(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given two stub columns where the outer ``Daerah`` label is written once per group.

    Expected:
    Rows below the outer label keep it.
    """
    path = make_workbook(
        indo=[
            ["Persentase"],
            [],
            ["Daerah", "Umur", 2009, 2010, 2011],
            ["Perkotaan", "10-24", 1, 2, 3],
            [None, "25-44", 4, 5, 6],
            ["Perdesaan", "10-24", 7, 8, 9],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    six = next(draft for draft in result.observations if draft.numeric_value == 6)
    assert _axes(six) == {"Daerah": "Perkotaan", "Umur": "25-44"}


def test_years_down_a_tahun_column(make_workbook: WorkbookFactory) -> None:
    """
    Condition:
    Given a ``Tahun`` column holding the period of each row, a ``Daerah`` column written once per group,
    and a notes block after the data.

    Expected:
    Each value column is a measure, each row takes its period from ``Tahun``, and the notes are not data.
    """
    path = make_workbook(
        indo=[
            ["Distribusi Pembagian Pengeluaran"],
            [],
            ["Daerah", "Tahun", "40% Terendah", "Indeks Gini"],
            ["Kota", 2010, 17.57, 0.38],
            [None, 2011, 16.1, 0.42],
            [None, "Maret 2012", 16.0, 0.42],
            ["Desa", 2010, 20.1, 0.3],
            [],
            ["Catatan", "1) angka sementara"],
        ]
    )
    result = parse_workbook(path, fallback_title="x")
    assert result.needs_review is False
    cells = {
        (draft.measure_label, draft.period_label, _axes(draft)["Daerah"]): draft.numeric_value
        for draft in result.observations
    }
    assert len(cells) == 8
    assert cells[("Indeks Gini", "Maret 2012", "Kota")] == 0.42
    assert cells[("40% Terendah", "2010", "Desa")] == 20.1


def test_duplicate_reason_rejects_two_members_on_one_axis() -> None:
    """
    Condition:
    Given a draft linked to two members of the same dimension.

    Expected:
    The duplicate check names the cell and the dimension, so the save never reaches the database.
    """
    province = DimensionDraft("provinsi", "Provinsi", "aceh", "Aceh", "geography", None)
    group = DimensionDraft("provinsi", "Provinsi", "makanan", "Makanan", "geography", None)
    draft = ObservationDraft(
        measure_source_code="m",
        measure_label="m",
        measure_unit="unspecified",
        measure_definition=None,
        measure_decimals=None,
        measure_note=None,
        source_value="1",
        numeric_value=1.0,
        canonical_value=None,
        canonical_unit=None,
        status_code=None,
        period_source_code="2025",
        period_label="2025",
        dimensions=(province, group),
        source_cell_key="indo:2025:4:1",
    )
    assert duplicate_reason([draft]) == "Source cell indo:2025:4:1 has two members on dimension provinsi"
