#!/usr/bin/env python3
"""Convert the legacy Numbers/xlsx pricing sheet → a cake-pricing JSON bundle.

The bundle is the SAME format the app's Settings → Import consumes
(app.services.portability, schema_version 1), so the initial data load reuses
the one import path (PLANNING.md §6).

What it imports from `Project-Ár`:
  * Components (row-2 headers from column L onward) + one COMPONENT_PRICES row
    each (base_amount = row 3, base_price = row 4).
  * Group assignment: Munkadíj/Rezsi → 'Alap'; boxes/plates → 'Doboz' (stock_item);
    everything else defaults to a catch-all group (refine by hand later).
It does NOT import the 112 historic offers (start fresh, §6).

stdlib only (zipfile + xml.etree) — no openpyxl. UTF-8 throughout.

Usage:
    python scripts/xlsx_to_bundle.py anon_tort.xlsx > bundle.json
Then in the app: Beállítások → Visszatöltés → upload bundle.json.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile

# defusedxml hardens stdlib ElementTree against XML entity/XXE attacks in a
# potentially untrusted .xlsx. API is identical to xml.etree.ElementTree.
import defusedxml.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Initial groups (must match the app seed / GROUPS table).
GROUPS = [
    {"id": 1, "name": "Alap", "sort_order": 10},
    {"id": 2, "name": "Piskóta", "sort_order": 20},
    {"id": 3, "name": "Töltelék", "sort_order": 30},
    {"id": 4, "name": "Burkolat", "sort_order": 40},
    {"id": 5, "name": "Dekor", "sort_order": 50},
    {"id": 6, "name": "Doboz", "sort_order": 60},
]
GROUP_ID = {g["name"]: g["id"] for g in GROUPS}

def _col_num(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def _load_rows(path: str, sheet_file: str) -> dict[int, dict[int, str]]:
    z = zipfile.ZipFile(path)
    shared = [
        "".join(t.text or "" for t in si.iter(f"{NS}t"))
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si")
    ]
    rows: dict[int, dict[int, str]] = {}
    root = ET.fromstring(z.read(sheet_file))
    for row in root.iter(f"{NS}row"):
        rn = int(row.get("r"))
        for c in row.findall(f"{NS}c"):
            ref, typ = c.get("r"), c.get("t")
            v, is_ = c.find(f"{NS}v"), c.find(f"{NS}is")
            if typ == "s" and v is not None:
                val = shared[int(v.text)]
            elif is_ is not None:
                val = "".join(x.text or "" for x in is_.iter(f"{NS}t"))
            elif v is not None:
                val = v.text
            else:
                val = ""
            rows.setdefault(rn, {})[_col_num(ref)] = val
    return rows


# Authoritative column→group ranges of the Project-Ár sheet (1-based, inclusive),
# provided by the product owner. Columns: H=8 K=11 L=12 AY=51 AZ=52 BS=71 BT=72
# CS=97 CT=98 DC=107 DD=108.
#   Alap    H–K   (8–11)   services (Munkadíj, Rezsi)
#   Doboz   L–AY  (12–51)  boxes / plates / stands  → stock items
#   Dekor   AZ–BS (52–71)  decorations
#   Piskóta BT–CS (72–97)
#   Burkolat CT–DC (98–107)
#   Töltelék DD–…  (108+)
_RANGES = [
    (8, 11, "Alap", "service"),
    (12, 51, "Doboz", "stock_item"),
    (52, 71, "Dekor", "ingredient"),
    (72, 97, "Piskóta", "ingredient"),
    (98, 107, "Burkolat", "ingredient"),
    (108, 10_000, "Töltelék", "ingredient"),
]


def _classify(col: int) -> tuple[int, str]:
    """Return (group_id, type) from the component's column, per the sheet ranges."""
    for lo, hi, group, ctype in _RANGES:
        if lo <= col <= hi:
            return GROUP_ID[group], ctype
    return GROUP_ID["Töltelék"], "ingredient"


def build_bundle(path: str) -> dict:
    rows = _load_rows(path, "xl/worksheets/sheet2.xml")  # Project-Ár
    r2, r3, r4 = rows.get(2, {}), rows.get(3, {}), rows.get(4, {})

    components: list[dict] = []
    prices: list[dict] = []
    cid = 0
    for col in sorted(r2):
        if col < 8:  # columns A–G are offer header, not components (Alap starts at H)
            continue
        name = (r2.get(col) or "").strip()
        if not name:
            continue
        base_amount = (r3.get(col) or "").strip() or "1"
        base_price = (r4.get(col) or "").strip() or "0"
        group_id, ctype = _classify(col)
        cid += 1
        components.append(
            {
                "id": cid, "name": name, "group_id": group_id,
                "unit": "g" if ctype == "ingredient" else "db",
                "type": ctype, "active": True, "notes": None,
            }
        )
        prices.append(
            {
                "id": cid, "component_id": cid,
                "base_amount": base_amount, "base_price": base_price,
                "effective_date": "2020-01-01T00:00:00+00:00", "expiration_date": None,
            }
        )

    return {
        "schema_version": 1,
        "exported_at": None,
        "tables": {
            "groups": GROUPS,
            "components": components,
            "component_prices": prices,
            "customers": [],
            "offers": [],
            "offer_components": [],
            "recipes": [],
            "recipe_items": [],
            "stock_movements": [],
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    bundle = build_bundle(sys.argv[1])
    sys.stdout.write(json.dumps(bundle, ensure_ascii=False, indent=2))
    print(
        f"\n# {len(bundle['tables']['components'])} components converted",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
