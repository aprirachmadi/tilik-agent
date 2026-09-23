"""Tests for BPS number, status, and year parsing."""

from __future__ import annotations

import pytest

from tilik_agent.ingestion.parsing import (
    canonical_member,
    classify_dimension_type,
    convert_value,
    is_status_token,
    parse_indonesian_number,
    published_years,
    year_in_text,
)


@pytest.mark.parametrize(
    ("text", "decimal_places", "expected"),
    [
        ("4.400", 0, 4400.0),
        ("5.695,9", 1, 5695.9),
        ("1,34", 2, 1.34),
        ("67,00", 2, 67.0),
        ("16.129", 0, 16129.0),
    ],
)
def test_parse_indonesian_number(text: str, decimal_places: int, expected: float) -> None:
    """
    Condition:
    Given SIMDASI source text and the column's decimal setting.

    Expected:
    Dots group thousands and a comma is the decimal mark.
    """
    assert parse_indonesian_number(text, decimal_places=decimal_places) == expected


@pytest.mark.parametrize(
    ("token", "letter_flags", "expected"),
    [
        ("n.a", False, True),
        ("–", False, True),
        ("0", False, False),
        ("e", True, True),
        ("e", False, False),
        ("...", True, True),
    ],
)
def test_status_tokens(token: str, letter_flags: bool, expected: bool) -> None:
    """
    Condition:
    Given a whole-cell marker from a static sheet or a SIMDASI column.

    Expected:
    Known markers are status flags, and a numeric zero is not a flag.
    """
    assert is_status_token(token, letter_flags=letter_flags) is expected


def test_percent_conversion() -> None:
    """
    Condition:
    Given a numeric percent and a non-numeric status cell.

    Expected:
    Only the numeric percent is scaled to a ratio.
    """
    assert convert_value(25.0, "Persen", None) == (0.25, "ratio")
    assert convert_value(None, "Persen", "n.a") == (None, None)


def test_dimension_roles_stay_generic_without_an_explicit_label() -> None:
    """
    Condition:
    Given a dimension label that only resembles a known category.

    Expected:
    Wilayah stays generic, while Jenis Kelamin and Provinsi receive their roles.
    """
    assert classify_dimension_type("Wilayah") == "generic_category"
    assert classify_dimension_type("Jenis Kelamin + Jumlah") == "sex"
    assert classify_dimension_type("Provinsi") == "geography"
    assert canonical_member("Laki-laki + Perempuan") == "total"
    assert canonical_member("Perempuan") == "female"


def test_published_years_and_title_years() -> None:
    """
    Condition:
    Given a catalog span and a title that hides a year inside SP2020.

    Expected:
    The span is inclusive, and SP2020 does not count as a standalone year.
    """
    assert published_years(2017, 2019) == [2017, 2018, 2019]
    assert published_years(None, None) == [None]
    assert year_in_text("Long Form SP2020 Menurut Umur") == []
    assert year_in_text("Pemilu Legislatif 2024") == [2024]
