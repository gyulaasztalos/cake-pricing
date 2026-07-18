"""Minimal i18n layer — externalized UI strings from day one (§5).

Deliberately dependency-free: a per-locale dict + a `t(key)` lookup. All
user-facing text goes through `t()` / the Jinja `t` global, so adding a second
language later is just adding another catalog (no template changes). Can be
swapped for gettext/Babel without touching call sites.
"""

from __future__ import annotations

from app.config import settings

# Hungarian catalog. Keys are dotted, stable identifiers (never shown raw).
HU: dict[str, str] = {
    # app / nav
    "app.title": "Torta árazó",
    "nav.offers": "Ajánlatok",
    "nav.customers": "Ügyfelek",
    "nav.components": "Összetevők",
    "nav.groups": "Csoportok",
    "nav.templates": "Sablonok",
    "nav.inventory": "Készlet",
    "nav.stats": "Statisztika",
    "nav.settings": "Beállítások",
    # generic actions
    "action.new": "Új",
    "action.save": "Mentés",
    "action.cancel": "Mégse",
    "action.edit": "Szerkesztés",
    "action.delete": "Törlés",
    "action.add": "Hozzáadás",
    "action.search": "Keresés",
    "action.confirm": "Megerősít",
    "common.yes": "Igen",
    "common.no": "Nem",
    "common.none": "—",
    "common.active": "aktív",
    "common.inactive": "inaktív",
    "common.currency": "Ft",
    "confirm.delete.title": "Biztosan törli?",
    # groups
    "groups.title": "Csoportok",
    "groups.new": "Új csoport",
    "groups.name": "Név",
    "groups.sort_order": "Sorrend",
    # components
    "components.title": "Összetevők",
    "components.new": "Új összetevő",
    "components.name": "Név",
    "components.group": "Csoport",
    "components.unit": "Egység",
    "components.type": "Típus",
    "components.type.ingredient": "Alapanyag",
    "components.type.service": "Szolgáltatás",
    "components.type.stock_item": "Készletcikk",
    "components.active": "Aktív",
    "components.notes": "Megjegyzés",
    "components.only_active": "Csak aktív",
    "components.current_price": "Aktuális ár",
    "components.price_change": "Ár módosítása",
    "components.price_history": "Ártörténet",
    "components.base_amount": "Alap mennyiség",
    "components.base_price": "Alap ár",
    "components.effective": "érvényes",
    "components.now": "most",
    # customers
    "customers.title": "Ügyfelek",
    "customers.new": "Új ügyfél",
    "customers.name": "Név",
    "customers.contact": "Elérhetőség",
    "customers.notes": "Megjegyzés",
    "customers.order_count": "rendelés",
    "customers.past_orders": "Korábbi rendelések",
    "customers.anonymize": "Névtelenítés",
    "customers.anonymized": "(névtelenített)",
    "customers.confirm_anonymize": (
        "Az ügyfél adatai névtelenítve lesznek (a rendelések megmaradnak)."
    ),
    # offers
    "offers.title": "Ajánlatok",
    "offers.new": "Új ajánlat",
    "offers.edit": "Ajánlat szerkesztése",
    "offers.customer": "Ügyfél",
    "offers.theme": "Téma",
    "offers.flavor": "Íz",
    "offers.due_date": "Határidő",
    "offers.entry_date": "Belépés",
    "offers.entry_date_hint": "nem módosítható — árazás dátuma",
    "offers.external": "külső ajánlatkérés",
    "offers.unpriced": "árazatlan",
    "offers.request_date": "Beérkezett",
    "offers.entry_date_unset": "az első mentéskor rögzül",
    "offers.status": "Státusz",
    "offers.status.draft": "Vázlat",
    "offers.status.sent": "Elküldve",
    "offers.status.accepted": "Elfogadva",
    "offers.status.rejected": "Elutasítva",
    "offers.status.done": "Kész",
    "offers.core_data": "Alapadatok",
    "offers.items": "Tételek",
    "offers.add_template": "Sablon hozzáadása",
    "offers.save_as_template": "Mentés sablonként",
    "offers.add_component": "Összetevő hozzáadása",
    "offers.calculated_price": "Számított ár",
    "offers.final_price": "Végleges ár",
    "offers.subtotal": "részösszeg",
    "offers.new_component_option": "[Új összetevő…]",
    "offers.new_customer_option": "[Új ügyfél…]",
    "offers.stock_warning": "készlet",
    # templates
    "templates.title": "Sablonok",
    "templates.new": "Új sablon",
    "templates.name": "Név",
    "templates.notes": "Megjegyzés",
    "templates.item_count": "tétel",
    # inventory
    "inventory.title": "Készlet",
    "inventory.receive": "Bevételezés",
    "inventory.on_hand": "Készleten",
    "inventory.state": "Állapot",
    "inventory.low": "fogyóban",
    "inventory.empty": "elfogyott",
    "inventory.movements": "Mozgások",
    "inventory.only_low": "Csak fogyóban",
    "inventory.reason.delivery": "Bevételezés",
    "inventory.reason.order": "Rendelés",
    "inventory.reason.correction": "Korrekció",
    "inventory.qty": "Mennyiség",
    # settings
    "settings.title": "Beállítások",
    "settings.export": "Adatmentés (Export)",
    "settings.import": "Visszatöltés (Import)",
    "settings.export_hint": "A teljes adatbázis letöltése egy fájlba (JSON).",
    "settings.import_hint": "Korábban exportált fájl visszatöltése.",
    # empty states
    "empty.no_results": "Nincs találat.",
}

CATALOGS: dict[str, dict[str, str]] = {"hu": HU}


def t(key: str, locale: str | None = None, **kwargs: object) -> str:
    """Translate a key. Falls back to the key itself if missing (visible in dev)."""
    cat = CATALOGS.get(locale or settings.default_locale, HU)
    text = cat.get(key, key)
    return text.format(**kwargs) if kwargs else text
