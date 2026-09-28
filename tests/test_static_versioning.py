"""Every stylesheet and script a template loads must carry ?v={{ version }}.

Without it a release is half-cached: 1.25.0 served the new offer form (with the
Fizetés button) next to the browser's cached 1.24 offer-form.js, which had no
handler for it — the button did nothing until a hard reload. A static scan of the
template SOURCES, so a page no test happens to render is covered too.
"""

from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
_REF = re.compile(r'(?:src|href)="(/static/[^"]+?\.(?:js|css)[^"]*)"')


def test_every_static_script_and_stylesheet_is_cache_busted():
    missing = [
        f"{path.relative_to(TEMPLATES)}: {url}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for url in _REF.findall(path.read_text(encoding="utf-8"))
        if "?v=" not in url
    ]
    assert not missing, "add ?v={{ version }} to: " + ", ".join(missing)


def test_the_scan_actually_finds_references():
    """Guard the guard: a broken regex would pass the test above vacuously."""
    found = [u for p in TEMPLATES.rglob("*.html") for u in _REF.findall(p.read_text("utf-8"))]
    assert any("offer-form.js" in u for u in found)
    assert any("app.css" in u for u in found)
