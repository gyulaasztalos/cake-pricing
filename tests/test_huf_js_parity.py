"""The live Ft/szelet note is formatted in JS; it must never disagree with Python.

`app/static/js/offer-form.js` mirrors `templating.format_huf` so the note the chef
sees while typing matches what the server renders after saving. Python's round() is
half-to-EVEN while JS Math.round is half-UP, so the JS carries its own
`roundHalfToEven` — this test executes the SHIPPED functions under node and compares
them against Python, which is what caught the 12500.5 → 12500/12501 split.

Skipped when node is unavailable (it is only needed for this parity check).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.templating import format_huf

_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "offer-form.js"

# Values chosen to cover grouping, truncation, and every half-way case (where the
# two languages' default rounding modes disagree).
CASES = [
    "1250",
    "833.3333333",
    "1000",
    "999",
    "1000000",
    "12500.5",  # the banker's-rounding trap: 12 500, not 12 501
    "0",
    "1",
    "0.5",
    "1.5",
    "2.5",
    "3.5",
    "7.5",
    "1041.6666667",
]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="requires node")


def _js_outputs(values: list[str]) -> list[str]:
    """Run the shipped formatHuf over `values` and return its results."""
    source = _JS.read_text(encoding="utf-8")
    # Take the real implementation, from the NBSP constant to the IIFE that uses it.
    start = source.index("const NBSP =")
    end = source.index("(function livePerPortion()")
    harness = (
        source[start:end]
        + f"const values = {json.dumps(values)};\n"
        + "console.log(JSON.stringify(values.map(v => formatHuf(parseFloat(v)))));\n"
    )
    done = subprocess.run(  # noqa: S603  # nosec B603 — fixed argv, no shell
        ["node", "-"],  # noqa: S607  # nosec B607 — resolved via PATH by design
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(done.stdout))


def test_js_formatting_matches_python_format_huf():
    js = _js_outputs(CASES)
    py = [format_huf(Decimal(v)) for v in CASES]
    mismatches = [(v, p, j) for v, p, j in zip(CASES, py, js, strict=True) if p != j]
    assert not mismatches, f"JS/Python formatting drift: {mismatches}"


def test_python_uses_bankers_rounding_as_the_js_assumes():
    """Guards the assumption the JS mirror is built on."""
    assert format_huf(Decimal("2.5")) == format_huf(Decimal("1.5"))  # both → 2
    assert format_huf(Decimal("12500.5")) == "12\xa0500\xa0Ft"
