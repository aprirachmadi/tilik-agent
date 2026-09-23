"""Tests for BPS client response handling."""

from __future__ import annotations

import pytest

from tilik_agent.ingestion.client import _html_error_page

_BPS_404 = (
    b"<!DOCTYPE html><html><head><title>Statistics Indonesia - Error</title></head><body>"
    b"<h1>Error 404</h1><div>Tidak bisa mengurai request &quot;tabelExcelIndo/Indo_6_1.xls&quot;</div>"
    b"<p>Badan Pusat Statistik (BPS - Statistics Indonesia), Jl. Dr. Sutomo 6-8</p></body></html>"
)


@pytest.mark.parametrize(
    "body,expected",
    [
        (_BPS_404, '404 Tidak bisa mengurai request "tabelExcelIndo/Indo_6_1.xls"'),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, None),
        (b"PK\x03\x04" + b"\x00" * 64, None),
        (b"<html><body><table><tr><td>Provinsi</td><td>2024</td></tr></table></body></html>", None),
    ],
)
def test_html_error_page(body: bytes, expected: str | None) -> None:
    """
    Condition:
    Given a BPS error page served for a broken workbook link, real xls and xlsx headers, and an HTML
    table without an error.

    Expected:
    Only the error page is recognised, and its message names the missing file.
    """
    assert _html_error_page(body) == expected
