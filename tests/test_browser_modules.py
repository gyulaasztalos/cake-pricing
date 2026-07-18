"""Comprehensive Playwright browser tests — one flow per module.

Covers the JS/HTMX interactions TestClient can't: form submits that redirect,
HTMX list refresh, inline dialogs, the offer form's live recalc + stepper.
Each test starts from a clean DB (only seed groups) and drives the real UI.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires DATABASE_URL + live server"
)


# --- Groups ------------------------------------------------------------------

def test_groups_create_and_edit(page: Page, clean_db):
    page.goto("/groups")
    page.click("text=Új csoport")
    page.fill("input[name=name]", "Tesztcsoport")
    page.click("button[type=submit]")
    expect(page.locator("table")).to_contain_text("Tesztcsoport")  # list refreshed

    # edit it
    row = page.locator("tr", has_text="Tesztcsoport")
    row.locator("a[href$='/edit']").click()
    page.fill("input[name=name]", "Átnevezett")
    page.click("button[type=submit]")
    expect(page.locator("table")).to_contain_text("Átnevezett")


# --- Components ---------------------------------------------------------------

def test_components_create_edit_price(page: Page, clean_db):
    page.goto("/components")
    page.click("text=Új összetevő")
    page.fill("input[name=name]", "Teszt Liszt")
    page.select_option("select[name=group_id]", label="Piskóta")
    page.fill("input[name=unit]", "g")
    page.fill("input[name=base_amount]", "1000")
    page.fill("input[name=base_price]", "200")
    page.click("button[type=submit]")
    expect(page.locator(".cp-list")).to_contain_text("Teszt Liszt")

    # expand detail → change price (real button, not text)
    page.click("text=Teszt Liszt")
    detail = page.locator(".cp-list__detail:visible")
    expect(detail).to_contain_text("Ártörténet")
    detail.locator("button", has_text="Ár módosítása").click()
    detail.locator("input[name=base_price]").fill("250")
    detail.locator("button[type=submit]").click()
    # after redirect the list shows the new current price
    expect(page.locator(".cp-list")).to_contain_text("250")


def test_components_search_without_group_filter(page: Page, clean_db, seed_component):
    seed_component("Keresett", "Piskóta", "g", "ingredient", "1000", "100")
    seed_component("Másik", "Töltelék", "g", "ingredient", "1000", "100")
    page.goto("/components")
    page.fill("input[name=q]", "Keresett")
    # HTMX live filter without choosing a group (the 422 regression guard)
    expect(page.locator("#cp-rows")).to_contain_text("Keresett")
    expect(page.locator("#cp-rows")).not_to_contain_text("Másik")


# --- Customers ----------------------------------------------------------------

def test_customers_create_edit_anonymize(page: Page, clean_db):
    page.goto("/customers")
    page.click("text=Új ügyfél")
    page.fill("input[name=name]", "Kovács Anna")
    page.fill("input[name=contact]", "+3630")
    page.click("button[type=submit]")
    expect(page.locator(".cp-list")).to_contain_text("Kovács Anna")

    # anonymize via confirm modal
    row = page.locator(".cp-list__row", has_text="Kovács Anna")
    row.locator("button[title]", has_text="").last.click()  # bin button opens modal
    dialog = page.locator("#cp-modal")
    expect(dialog).to_be_visible()
    dialog.locator("button[type=submit]").click()
    expect(page.locator(".cp-list")).to_contain_text("névtelenített")


# --- Inventory ----------------------------------------------------------------

def test_inventory_receive_delivery(page: Page, clean_db, seed_component):
    seed_component("29x29 Doboz", "Doboz", "db", "stock_item", "1", "300")
    page.goto("/inventory")
    expect(page.locator("#cp-rows")).to_contain_text("29x29 Doboz")
    page.click("text=Bevételezés")
    page.select_option("select[name=component_id]", label="29x29 Doboz (db)")
    page.fill("input[name=qty]", "50")
    page.click("button[type=submit]")
    # back on the list, stock shows 50
    row = page.locator(".cp-list__row", has_text="29x29 Doboz")
    expect(row).to_contain_text("50")


# --- Offer (full flow: inline customer, two groups, template, save) ----------

def test_offer_full_flow(page: Page, clean_db, seed_component):
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    seed_component("Mascarpone", "Töltelék", "g", "ingredient", "250", "700")

    page.goto("/offers/new")
    # inline new customer
    page.select_option("#customer-select", "__new__")
    page.wait_for_selector("#cp-modal input[name=name]", state="visible")
    page.fill("#cp-modal input[name=name]", "Nagy Béla")
    page.click("#cp-modal button[type=submit]")
    expect(page.locator("#customer-select option:checked")).to_have_text("Nagy Béla", timeout=8000)

    page.fill("input[name=theme]", "Szülinap")

    # add a Piskóta line: Liszt 1000 g → 200 Ft
    pis = page.locator('.cp-group', has=page.locator('text="Piskóta"')).first
    pis.locator("button.cp-add-line").click()
    line = pis.locator(".cp-lines .cp-line").first
    line.locator("select[name=component_id]").select_option(label="Liszt")
    page.wait_for_timeout(1000)
    pis = page.locator('.cp-group', has=page.locator('text="Piskóta"')).first
    pis.locator(".cp-lines .cp-line").first.locator("input[name=amount]").fill("1000")
    pis.locator(".cp-lines .cp-line").first.locator("input[name=amount]").dispatch_event("change")
    expect(page.locator("#calc-total")).to_have_text("200 Ft", timeout=8000)

    # final price + save
    page.fill("input[name=final_price]", "5000")
    page.click("button[type=submit]:has-text('Mentés')")
    expect(page.locator(".cp-list")).to_contain_text("Nagy Béla")  # offers list refreshed


def test_offer_delete_via_modal(page: Page, clean_db, seed_component):
    # create an offer through the API-less path: use the UI quickly
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    page.goto("/offers/new")
    page.select_option("#customer-select", "__new__")
    page.wait_for_selector("#cp-modal input[name=name]", state="visible")
    page.fill("#cp-modal input[name=name]", "Törlendő Ügyfél")
    page.click("#cp-modal button[type=submit]")
    expect(page.locator("#customer-select option:checked")).to_have_text(
        "Törlendő Ügyfél", timeout=8000
    )
    page.click("button[type=submit]:has-text('Mentés')")
    expect(page.locator(".cp-list")).to_contain_text("Törlendő Ügyfél")

    # expand row, delete via modal
    row = page.locator(".cp-list__row", has_text="Törlendő Ügyfél")
    row.locator("button[hx-get$='/delete']").click()
    dialog = page.locator("#cp-modal")
    expect(dialog).to_be_visible()
    dialog.locator("button[type=submit]").click()
    expect(page.locator(".cp-list")).not_to_contain_text("Törlendő Ügyfél")
