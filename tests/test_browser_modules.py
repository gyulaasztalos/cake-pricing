"""Comprehensive Playwright browser tests — one flow per module.

Covers the JS/HTMX interactions TestClient can't: form submits that redirect,
HTMX list refresh, inline dialogs, the offer form's live recalc + stepper.
Each test starts from a clean DB (only seed groups) and drives the real UI.
"""

from __future__ import annotations

import datetime as dt
import os
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires DATABASE_URL + live server"
)


def _submit(page: Page, selector: str = "button[type=submit]", *, to: str | None = None) -> None:
    """Click a full-page form submit and wait for the POST→303→GET to land.

    run_and_wait_for the final list navigation catches the whole redirect chain;
    `to` (a URL glob) pins the expected destination when given.
    """
    if to:
        with page.expect_navigation(url=to):
            page.click(selector)
    else:
        with page.expect_navigation():
            page.click(selector)


def _fill(page: Page, selector: str, value: str) -> None:
    """Fill an input and assert the value stuck (guards against focus/timing races
    on autofocused, prefilled edit forms)."""
    field = page.locator(selector)
    field.click()
    field.fill(value)
    expect(field).to_have_value(value)


def _search(page: Page, value: str) -> None:
    """Type into a search box so the HTMX `keyup` trigger actually fires
    (page.fill sets the value without emitting keyup)."""
    box = page.locator("input[name=q]")
    box.click()
    box.press_sequentially(value)


# --- Groups ------------------------------------------------------------------


def test_groups_create_and_edit(page: Page, clean_db):
    page.goto("/groups")
    page.click("text=Új csoport")
    page.wait_for_url("**/groups/new")
    page.fill("input[name=name]", "Tesztcsoport")
    _submit(page, to="**/groups")
    expect(page.locator("table")).to_contain_text("Tesztcsoport")

    page.locator("tr", has_text="Tesztcsoport").locator("a[href$='/edit']").click()
    page.wait_for_url("**/edit")
    _fill(page, "input[name=name]", "Átnevezett")
    _submit(page, to="**/groups")
    expect(page.locator("table")).to_contain_text("Átnevezett")


# --- Components ---------------------------------------------------------------


def test_components_create_edit_price(page: Page, clean_db):
    page.goto("/components")
    with page.expect_navigation():
        page.click("text=Új összetevő")
    page.fill("input[name=name]", "Teszt Liszt")
    page.select_option("select[name=group_id]", label="Piskóta")
    page.select_option("select[name=unit]", "g")
    page.fill("input[name=base_amount]", "1000")
    page.fill("input[name=base_price]", "200")
    _submit(page)
    expect(page.locator(".cp-list")).to_contain_text("Teszt Liszt")

    # expand detail (HTMX) → change price via the real button
    page.click("text=Teszt Liszt")
    detail = page.locator(".cp-list__detail:visible")
    expect(detail).to_contain_text("Ártörténet")
    detail.locator("button", has_text="Ár módosítása").click()
    detail.locator("input[name=base_price]").fill("250")
    _submit(page, ".cp-list__detail:visible button[type=submit]")
    expect(page.locator(".cp-list")).to_contain_text("250")


def test_components_search_without_group_filter(page: Page, clean_db, seed_component):
    seed_component("Keresett", "Piskóta", "g", "ingredient", "1000", "100")
    seed_component("Másik", "Töltelék", "g", "ingredient", "1000", "100")
    page.goto("/components")
    _search(page, "Keresett")  # HTMX live filter (keyup), no group chosen
    expect(page.locator("#cp-rows")).to_contain_text("Keresett")
    expect(page.locator("#cp-rows")).not_to_contain_text("Másik")


# --- Customers ----------------------------------------------------------------


def test_customers_create_edit_anonymize(page: Page, clean_db):
    page.goto("/customers")
    with page.expect_navigation():
        page.click("text=Új ügyfél")
    page.fill("input[name=name]", "Kovács Anna")
    page.fill("input[name=contact]", "+3630")
    _submit(page)
    expect(page.locator(".cp-list")).to_contain_text("Kovács Anna")

    # anonymize via confirm modal
    row = page.locator(".cp-list__row", has_text="Kovács Anna")
    row.get_by_title("Törlés").click()
    dialog = page.locator("#cp-modal")
    expect(dialog).to_be_visible()
    _submit(page, "#cp-modal button[type=submit]")
    # PII scrubbed → the name becomes the stable "Anon<id>" label.
    expect(page.locator(".cp-list")).not_to_contain_text("Kovács Anna")
    expect(page.locator(".cp-list")).to_contain_text(re.compile(r"Anon\d+"))


# --- Inventory ----------------------------------------------------------------


def test_inventory_receive_delivery(page: Page, clean_db, seed_component):
    seed_component("29x29 Doboz", "Doboz", "db", "stock_item", "1", "300")
    page.goto("/inventory")
    expect(page.locator("#cp-rows")).to_contain_text("29x29 Doboz")
    with page.expect_navigation():
        page.click("text=Bevételezés")
    page.select_option("select[name=component_id]", label="29x29 Doboz (db)")
    page.fill("input[name=qty]", "50")
    _submit(page)
    expect(page.locator(".cp-list__row", has_text="29x29 Doboz")).to_contain_text("50")


# --- Templates ----------------------------------------------------------------


def test_recipes_edit_and_delete(page: Page, clean_db, seed_component):
    from app.db import SessionLocal
    from app.models import Recipe

    cid = seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    s = SessionLocal()
    from decimal import Decimal

    from app.models import RecipeItem

    r = Recipe(name="12 szeletes teszt")
    s.add(r)
    s.flush()
    s.add(RecipeItem(recipe_id=r.id, component_id=cid, amount=Decimal("500")))
    s.commit()
    s.close()

    page.goto("/recipes")
    expect(page.locator(".cp-list")).to_contain_text("12 szeletes teszt")
    page.locator(".cp-list__row", has_text="12 szeletes teszt").locator("a[href$='/edit']").click()
    page.wait_for_url("**/edit")
    page.fill("input[name=name]", "16 szeletes teszt")
    _submit(page)
    expect(page.locator(".cp-list")).to_contain_text("16 szeletes teszt")

    # delete via modal
    row = page.locator(".cp-list__row", has_text="16 szeletes teszt")
    row.get_by_title("Törlés").click()
    expect(page.locator("#cp-modal")).to_be_visible()
    _submit(page, "#cp-modal button[type=submit]")
    expect(page.locator(".cp-list")).not_to_contain_text("16 szeletes teszt")


# --- Offer (full flow: inline customer, live recalc, save) -------------------


def _add_inline_customer(page: Page, name: str) -> None:
    page.select_option("#customer-select", "__new__")
    page.wait_for_selector("#cp-modal input[name=name]", state="visible")
    page.fill("#cp-modal input[name=name]", name)
    page.click("#cp-modal button[type=submit]")
    expect(page.locator("#customer-select option:checked")).to_have_text(name, timeout=8000)


def test_offer_full_flow(page: Page, clean_db, seed_component):
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")

    page.goto("/offers/new")
    _add_inline_customer(page, "Nagy Béla")
    page.fill("input[name=theme]", "Szülinap")

    pis = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
    pis.locator("button.cp-add-line").click()
    pis.locator(".cp-lines .cp-line").first.locator("select[name=component_id]").select_option(
        label="Liszt"
    )
    page.wait_for_timeout(1000)
    pis = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
    amount = pis.locator(".cp-lines .cp-line").first.locator("input[name=amount]")
    amount.fill("1000")
    amount.dispatch_event("change")
    expect(page.locator("#calc-total")).to_have_text("200 Ft", timeout=8000)

    page.fill("input[name=final_price]", "5000")
    _submit(page, "button[type=submit]:has-text('Mentés')")
    expect(page.locator(".cp-list")).to_contain_text("Nagy Béla")


def test_offer_delete_via_modal(page: Page, clean_db, seed_component):
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    page.goto("/offers/new")
    _add_inline_customer(page, "Törlendő Ügyfél")
    _submit(page, "button[type=submit]:has-text('Mentés')")
    expect(page.locator(".cp-list")).to_contain_text("Törlendő Ügyfél")

    row = page.locator(".cp-list__row", has_text="Törlendő Ügyfél")
    row.get_by_title("Törlés").click()
    expect(page.locator("#cp-modal")).to_be_visible()
    _submit(page, "#cp-modal button[type=submit]")
    expect(page.locator(".cp-list")).not_to_contain_text("Törlendő Ügyfél")


def test_per_portion_note_updates_live(page: Page, clean_db):
    """The Ft/szelet note must react to typing, not only to a save round-trip.

    Server-rendered on load (works with JS off); offer-form.js keeps it live. NBSP
    is what format_huf emits, so assert on the exact characters.
    """
    page.goto("/offers/new")
    note = page.locator("#per-portion")
    expect(note).to_have_text("")  # nothing yet: neither field is filled

    page.fill("#final-price", "15000")
    expect(note).to_have_text("")  # still no slice count → no per-slice price
    page.fill("#portions", "12")
    expect(note).to_have_text("1 250 Ft / szelet")

    # Reacts to either field changing.
    page.fill("#portions", "10")
    expect(note).to_have_text("1 500 Ft / szelet")
    page.fill("#final-price", "20000")
    expect(note).to_have_text("2 000 Ft / szelet")

    # Clearing a field withdraws the note rather than showing a bogus number.
    page.fill("#portions", "")
    expect(note).to_have_text("")


def test_sort_toggle_shows_one_arrow_and_flips_order(page: Page, clean_db):
    """The icon toggle must render a real lucide arrow, swap on click, and re-sort."""
    from app.db import SessionLocal
    from app.models import Customer, Offer

    s = SessionLocal()
    try:
        c = Customer(name="Sorrend")
        s.add(c)
        s.flush()
        for theme, day in (("kesoi", 30), ("korai", 1)):
            s.add(
                Offer(
                    customer_id=c.id,
                    theme=theme,
                    status="sent",
                    due_date=dt.datetime(2026, 9, day, tzinfo=dt.UTC),
                )
            )
        s.commit()
    finally:
        s.close()

    page.goto("/offers")
    toggle = page.locator(".cp-sort-toggle")
    asc, desc = toggle.locator(".cp-sort-asc"), toggle.locator(".cp-sort-desc")

    # lucide replaced the placeholder with a real <svg> (wrong icon name = no svg).
    expect(asc.locator("svg")).to_have_count(1)
    expect(desc.locator("svg")).to_have_count(1)
    # Exactly one arrow visible: ascending by default.
    expect(asc).to_be_visible()
    expect(desc).to_be_hidden()
    expect(page.locator(".cp-list__row").first).to_contain_text("korai")

    toggle.click()
    # Icon swaps...
    expect(desc).to_be_visible()
    expect(asc).to_be_hidden()
    # ...and the list actually re-sorted (HTMX refreshed #cp-rows).
    expect(page.locator(".cp-list__row").first).to_contain_text("kesoi", timeout=8000)


def test_filter_bar_controls_are_aligned(page: Page, clean_db):
    """Every control in the offers filter bar shares one baseline and height.

    Pico gives form controls a bottom margin for stacked forms; in this flex row
    `align-items: center` centres the MARGIN box, so a control that keeps the
    margin sits visibly higher than one without it (this misaligned the sort
    toggle). The icon also needs a full line box or the button ends up shorter.
    """
    page.goto("/offers")
    boxes = {
        name: page.locator(sel).bounding_box()
        for name, sel in {
            "search": "form.cp-topbar input[name=q]",
            "status": "form.cp-topbar [data-filter=status] > summary",
            "sort": "form.cp-topbar .cp-sort-toggle",
            "year": "form.cp-topbar [data-filter=year] > summary",
        }.items()
    }
    centres = [b["y"] + b["height"] / 2 for b in boxes.values()]
    heights = [b["height"] for b in boxes.values()]
    assert max(centres) - min(centres) <= 1.5, f"vertically misaligned: {boxes}"
    assert max(heights) - min(heights) <= 1.5, f"heights differ: {boxes}"


def test_profit_pct_and_final_price_are_bound(page: Page, clean_db, seed_component):
    """profit% ⇄ final price, and what a cost-base change follows (last edited)."""
    # Munkadíj 10 000 alone makes the cost base exactly 10 000 Ft.
    seed_component("Munkadíj", "Alap", "db", "service", "1", "10000")
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "2000")

    page.goto("/offers/new")
    pct, price = page.locator("#profit-pct"), page.locator("#final-price")
    expect(page.locator("#calc-total")).to_have_text("10 000 Ft")
    # Prefill: default 10% → 11 000 Ft.
    expect(pct).to_have_value("10")
    expect(price).to_have_value("11000")

    # Overwrite the price → the % follows.
    price.fill("12000")
    expect(pct).to_have_value("20")

    # Overwrite the % → the price follows.
    pct.fill("15")
    expect(price).to_have_value("11500")

    # Cost base changes; the % was edited last, so the PRICE re-derives.
    pis = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
    pis.locator("button.cp-add-line").click()
    page.wait_for_timeout(500)
    piskota = page.locator(".cp-group", has=page.locator('text="Piskóta"'))
    piskota.first.locator(".cp-line").first.locator("select[name=component_id]").select_option(
        label="Liszt"
    )
    page.wait_for_timeout(800)
    amount = piskota.first.locator(".cp-line").first.locator("input[name=amount]")
    amount.fill("1000")
    amount.dispatch_event("change")
    # 10 000 + (1000 g × 2000 Ft/1000 g) = 12 000 → still 15% → 13 800
    expect(page.locator("#calc-total")).to_have_text("12 000 Ft", timeout=8000)
    expect(price).to_have_value("13800")
    expect(pct).to_have_value("15")


def test_offer_footer_fits_on_a_phone(page: Page, clean_db):
    """The pricing footer must wrap on mobile.

    Regression: it was `display:flex` with no wrap, so `justify-content:flex-end`
    pushed Számított ár and Nyereség % off the LEFT edge (x ≈ -182px) — invisible
    AND unreachable, because overflow in that direction does not scroll.
    """
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto("/offers/new")
    fields = page.locator(".cp-offer-footer > *")
    assert fields.count() == 4
    rows = set()
    for i in range(fields.count()):
        box = fields.nth(i).bounding_box()
        assert box["x"] >= -1, f"field {i} starts off-screen at x={box['x']}"
        assert box["x"] + box["width"] <= 391, f"field {i} overflows right"
        rows.add(round(box["y"]))
    assert len(rows) > 1, "expected the footer to wrap onto multiple rows on mobile"


def test_offer_footer_stays_one_row_on_desktop(page: Page, clean_db):
    """The mobile fix must not push the desktop footer onto extra rows —
    nowrap lets the fields shrink into a single tidy row there."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto("/offers/new")
    fields = page.locator(".cp-offer-footer > *")
    rows = {round(fields.nth(i).bounding_box()["y"]) for i in range(fields.count())}
    assert len(rows) == 1, f"desktop footer wrapped onto {len(rows)} rows"


MOBILE_PAGES = [
    "/offers",
    "/offers/new",
    "/schedule",
    "/customers",
    "/customers/new",
    "/components",
    "/components/new",
    "/recipes",
    "/inventory",
    "/inventory/receive",
    "/groups",
    "/groups/new",
    "/stats",
    "/settings",
]


@pytest.mark.parametrize("path", MOBILE_PAGES)
def test_no_layout_escapes_the_phone_viewport(page: Page, clean_db, seed_component, path):
    """Site-wide guard: nothing may sit outside a 390px viewport.

    Element boxes are checked, not just document.scrollWidth — overflow to the
    LEFT does not create a scrollbar, so it is invisible AND unreachable (exactly
    how the offer footer and the year filter broke). The sidebar is excluded: on
    mobile it is an off-canvas drawer, off-screen by design.
    """
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(path)
    page.wait_for_timeout(250)
    offenders = page.evaluate("""() => {
      const vw = innerWidth, out = [];
      document.querySelectorAll('body *').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return;
        if (getComputedStyle(el).visibility === 'hidden') return;
        if (el.closest('.cp-sidebar')) return;          // off-canvas by design
        if (r.left < -1 || r.right > vw + 1) {
          out.push(el.tagName.toLowerCase() + '.' + (el.className || '').toString().slice(0, 30)
                   + ' [' + Math.round(r.left) + '..' + Math.round(r.right) + ']');
        }
      });
      return out;
    }""")
    assert not offenders, f"{path} overflows 390px: {offenders[:5]}"


@pytest.mark.parametrize("width", [390, 1280])
def test_filter_dropdown_panels_stay_on_screen(page: Page, clean_db, width):
    """An OPEN dropdown must fit too — the year panel used to run 78px past the
    right edge on desktop, and flipping its anchor pushed it off the left on
    mobile. Mobile now flows the panel inline; desktop anchors it right."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("/offers")
    filters = page.locator(".cp-filter")
    assert filters.count() >= 2  # status + year
    for i in range(filters.count()):
        f = filters.nth(i)
        name = f.get_attribute("data-filter")
        f.locator("summary").click()
        page.wait_for_timeout(150)
        box = f.locator(".cp-filter__menu").bounding_box()
        assert box["x"] >= -1, f"[{name}] panel off the left at {box['x']} ({width}px)"
        assert box["x"] + box["width"] <= width + 1, f"[{name}] panel off the right ({width}px)"
        f.locator("summary").click()
        page.wait_for_timeout(100)
