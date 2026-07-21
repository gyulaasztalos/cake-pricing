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
    "nav.calendar": "Naptár",
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
    "components.product_id": "Termék azonosító",
    "components.product_id_hint": "az árfigyelő termékkódja (opcionális)",
    "components.price_missing": (
        "A termékazonosító nem található a legutóbbi árfigyelő fájlban — az ár nem frissült."
    ),
    "components.price_missing_short": "ár nem található",
    # customers
    "customers.title": "Ügyfelek",
    "customers.new": "Új ügyfél",
    "customers.name": "Név",
    "customers.contact": "Elérhetőség",
    "customers.notes": "Megjegyzés",
    "customers.order_count": "rendelés",
    "customers.past_orders": "Korábbi rendelések",
    "customers.anonymize": "Névtelenítés",
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
    "offers.entry_date": "Létrehozva",
    "offers.entry_date_hint": "nem módosítható — árazás dátuma",
    "offers.external": "külső ajánlatkérés",
    "offers.notes": "Megjegyzés",
    "offers.notes_from_customer": "az ügyfél leírása",
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
    "settings.about": "Névjegy",
    "settings.version": "Verzió",
    # calendar
    "calendar.title": "Naptár",
    "calendar.feed_name": "Anita Tortái — határidők",
    "calendar.prev_month": "Előző hónap",
    "calendar.next_month": "Következő hónap",
    "calendar.today": "Ma",
    "calendar.new_offer": "Új ajánlat erre a napra",
    "calendar.excluded_note": "Az elutasított ajánlatok nem jelennek meg a naptárban.",
    # calendar feed (subscription lives on the Beállítások page)
    "calendar.subscribe": "Naptár-feliratkozás",
    "calendar.subscribe_hint": (
        "Apple Naptár: Fájl → Új naptárelőfizetés… és illeszd be ezt a címet. "
        "A határidők ezután megjelennek a telefonodon és a gépeden is, és "
        "automatikusan frissülnek."
    ),
    "calendar.secret_warning": (
        "Ez a cím titkos: aki ismeri, látja az ügyfélneveket és az árakat. Ne oszd meg!"
    ),
    "calendar.disabled": ("A naptár-feliratkozás nincs beállítva (hiányzik a CALENDAR_TOKEN)."),
    # stats
    "stats.title": "Statisztika",
    "stats.all_years": "Összes év",
    "stats.scope.all": "Összesített adatok (minden év)",
    "stats.scope.year": "{year}. évi adatok",
    "stats.kpi.offers": "Ajánlatok",
    "stats.kpi.won": "Elnyert (elfogadva + kész)",
    "stats.kpi.winrate": "Nyerési arány",
    "stats.kpi.winrate_hint": "elnyert / elküldött ajánlatok",
    "stats.kpi.revenue": "Bevétel (elnyert)",
    "stats.kpi.cost": "Számított költség",
    "stats.kpi.margin": "Árrés",
    "stats.kpi.avg": "Átlagos ajánlatérték",
    "stats.kpi.drafts": "Vázlatok",
    "stats.kpi.new_customers": "Új ügyfelek",
    "stats.chart.revenue_year": "Éves bevétel",
    "stats.chart.revenue_month": "Havi bevétel",
    "stats.chart.offers_year": "Ajánlatok évente (elnyert kiemelve)",
    "stats.chart.offers_month": "Ajánlatok havonta (elnyert kiemelve)",
    "stats.status": "Ajánlatok státusz szerint",
    "stats.flavors": "Legnépszerűbb ízek",
    "stats.themes": "Leggyakoribb témák",
    "stats.source": "Forrás",
    "stats.source.internal": "Belső",
    "stats.source.external": "Weboldal (külső)",
    "stats.count": "db",
    "stats.empty": "Még nincs adat.",
    # price-report e-mail
    "email.footer": "Anita Tortái — automatikus árfrissítés",
    "email.price_report.subject": "Árfrissítés: {changed} módosult, {missing} nem található",
    "email.price_report.heading": "Napi árfrissítés",
    "email.price_report.intro": "{checked} termékazonosítóval rendelkező összetevő ellenőrizve.",
    "email.price_report.changes_heading": "Módosult árak ({n})",
    "email.price_report.col_name": "Összetevő",
    "email.price_report.col_old": "Régi ár",
    "email.price_report.col_new": "Új ár",
    "email.price_report.missing_heading": "Nem található termékazonosítók ({n})",
    "email.price_report.missing_note": (
        "Ezek az összetevők termékazonosítóval rendelkeznek, de a mai árfigyelő "
        "fájlban nem szerepeltek. Ellenőrizd a kódot az összetevőnél."
    ),
    # empty states
    "empty.no_results": "Nincs találat.",
}

CATALOGS: dict[str, dict[str, str]] = {"hu": HU}


def t(key: str, locale: str | None = None, **kwargs: object) -> str:
    """Translate a key. Falls back to the key itself if missing (visible in dev)."""
    cat = CATALOGS.get(locale or settings.default_locale, HU)
    text = cat.get(key, key)
    return text.format(**kwargs) if kwargs else text
