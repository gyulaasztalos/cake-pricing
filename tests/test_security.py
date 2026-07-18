"""Security regression tests."""

from __future__ import annotations

import json


def test_comps_json_escapes_script_breakout():
    """A component name containing </script> must not break out of the inline
    <script> block (stored XSS). Escaped output must still be valid JSON."""
    from app.routers.offers import _comps_json

    class _C:
        def __init__(self, i, n, u):
            self.id, self.name, self.unit = i, n, u

    evil = _C(1, "</script><img src=x onerror=alert(1)>", "g")
    out = _comps_json({2: [evil]})

    # No raw breakout sequences survive.
    assert "</script>" not in out
    assert "<" not in out and ">" not in out
    # Still valid JSON that decodes back to the original name.
    assert json.loads(out)["2"][0]["name"] == "</script><img src=x onerror=alert(1)>"
