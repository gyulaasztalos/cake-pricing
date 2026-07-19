"""Playwright browser test of the offer-form JS interactions.

Run manually against a live app (not part of `pytest` unit runs — needs a
browser + running server + DB):

    DATABASE_URL=postgresql+psycopg://cake:devpass@localhost:55432/cake-pricing \\
        uv run uvicorn app.main:app --port 8140 &
    BASE_URL=http://localhost:8140 uv run python tests/browser_offer_form.py

Exercises the behaviors TestClient can't: per-group multi add-line, unit-aware
stepper, inline new-customer / new-component dialogs, live recalc.
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import expect, sync_playwright

BASE = os.getenv("BASE_URL", "http://localhost:8140")


def run() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{BASE}/offers/new")

        # --- inline new-customer create ---
        page.select_option("#customer-select", "__new__")
        page.wait_for_selector("#cp-modal input[name=name]", state="visible")
        page.fill("#cp-modal input[name=name]", "Böszörményi Ágnes")
        page.click("#cp-modal button[type=submit]")
        # after creation the option is added + selected, modal closes
        expect(page.locator("#customer-select option:checked")).to_have_text(
            "Böszörményi Ágnes", timeout=8000
        )
        print("OK inline new-customer created + auto-selected")

        # --- Piskóta group: add TWO lines (the 'only one line' bug) ---
        piskota = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
        add_btn = piskota.locator("button.cp-add-line")
        add_btn.click()
        add_btn.click()
        lines = piskota.locator(".cp-lines .cp-line")
        count = lines.count()
        assert count >= 2, f"expected >=2 lines after two adds, got {count}"
        print(f"OK per-group add-line works: {count} lines in Piskóta")

        # --- choose Liszt in the first line, set amount, expect a live cost ---
        first = lines.first
        first.locator("select[name=component_id]").select_option(label="Liszt")
        # let the select-triggered recalc settle (it swaps #sections)
        page.wait_for_timeout(1200)
        piskota = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
        first = piskota.locator(".cp-lines .cp-line").first
        amt = first.locator("input[name=amount]")
        amt.fill("2000")
        amt.dispatch_event("change")
        # Liszt is 187 Ft / 1000 g → 2000 g = 374 Ft; and exactly ONE line (no dup)
        expect(page.locator("#calc-total")).to_have_text("374 Ft", timeout=8000)
        piskota = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
        assert piskota.locator(".cp-lines .cp-line").count() == 1, "recalc duplicated the line"
        print("OK live recalc: 2000 g Liszt → 374 Ft, no line duplication")

        # --- unit-aware stepper: Liszt is grams → step should be >1 ---
        # (re-locate after swap)
        piskota = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
        gram_line = piskota.locator(".cp-line").first
        before = gram_line.locator("input[name=amount]").input_value()
        gram_line.locator(".cp-stepper button").first.click()  # ▲
        page.wait_for_timeout(300)
        piskota = page.locator(".cp-group", has=page.locator('text="Piskóta"')).first
        after = piskota.locator(".cp-line").first.locator("input[name=amount]").input_value()
        print(f"stepper grams: {before} -> {after}")
        assert float(after) - float(before) >= 10, "gram step should be >=10"
        print("OK unit-aware stepper (grams step >1)")

        # --- inline new-component in a group ---
        burkolat = page.locator(".cp-group", has=page.locator('text="Burkolat"')).first
        burkolat.locator("button.cp-add-line").click()
        newline = burkolat.locator(".cp-lines .cp-line").last
        newline.locator("select[name=component_id]").select_option("__new__")
        expect(page.locator("#cp-modal")).to_be_visible()
        page.fill("#cp-modal input[name=name]", "Tükörzselé")
        page.click("#cp-modal button[type=submit]")
        page.wait_for_timeout(500)
        assert page.locator("select[name=component_id] option", has_text="Tükörzselé").count() > 0
        print("OK inline new-component created + added to group picker")

        # --- save-as-template dialog is inline (not window.prompt) ---
        page.click("button:has-text('Mentés sablonként')")
        expect(page.locator("#tpl-save-dialog")).to_be_visible()
        print("OK save-as-template inline dialog opens")

        browser.close()
    print("\n*** BROWSER TESTS PASSED ***")
    return 0


if __name__ == "__main__":
    sys.exit(run())
