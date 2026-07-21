"""Amount / price parsing: Hungarian decimal comma + space thousands, negatives.

Pure unit tests (no DB) over the offer form's number parsing helpers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.routers._helpers import decimal_hu
from app.routers.offers import _parse_decimal, _parse_lines
from app.templating import format_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2,5", Decimal("2.5")),  # decimal comma
        ("2.5", Decimal("2.5")),  # decimal period still works
        ("45 000,50", Decimal("45000.50")),  # space thousands + comma
        ("1 000", Decimal("1000")),  # non-breaking space thousands
        (" 12 ", Decimal("12")),  # surrounding whitespace
        ("1000", Decimal("1000")),
        ("2,", Decimal("2")),  # trailing comma (mid-typing) -> 2.
    ],
)
def test_decimal_hu_accepts_hungarian_notation(raw, expected):
    assert decimal_hu(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "abc", "2,,3", "1.2.3"])
def test_decimal_hu_rejects_blank_and_garbage(raw):
    assert decimal_hu(raw) is None


def test_parse_lines_normalizes_comma_and_drops_negatives_and_blanks():
    pairs = _parse_lines(
        component_ids=["7", "8", "9", "", "10"],
        amounts=["2,5", "-1", "", "3", "abc"],
    )
    # 7 -> comma parsed; 8 -> negative dropped; 9 -> blank counts as 0;
    # "" component skipped entirely; 10 -> garbage dropped.
    assert pairs == [(7, Decimal("2.5")), (9, Decimal("0"))]


def test_parse_decimal_final_price_accepts_comma_and_spaces():
    assert _parse_decimal("45 000,50") == Decimal("45000.50")
    assert _parse_decimal("") is None
    assert _parse_decimal("nonsense") is None


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("2.500", "2.5"),
        ("3.210", "3.21"),
        ("1.000", "1"),
        ("1000.000", "1000"),
        ("0.00", "0"),
    ],
)
def test_format_amount_trims_trailing_zeros(raw, shown):
    assert format_amount(Decimal(raw)) == shown
