"""Ajánlatok (Offers) — list, create/edit form, live recalc, templates, delete."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import extract, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_session
from app.i18n import t
from app.models import Component, Customer, Group, Offer, Recipe, RecipeItem
from app.routers._helpers import decimal_hu, get_or_404, return_to, see_other, see_other_back
from app.services import offers as offer_svc
from app.templating import templates

router = APIRouter()

STATUSES = ["draft", "sent", "accepted", "rejected", "done"]

# The base-cost group (Munkadíj, Rezsi) — added fresh to every offer, so it is
# never saved into a Recept (recipe). Identified by its seeded name (§3.1/§3.2).
BASE_GROUP_NAME = "Alap"


def _comps_by_group(session: Session) -> dict[int, list[Component]]:
    grouped: dict[int, list[Component]] = {}
    for c in session.scalars(
        select(Component).where(Component.active.is_(True)).order_by(Component.name)
    ):
        grouped.setdefault(c.group_id, []).append(c)
    return grouped


def _comps_json(comps_by_group: dict[int, list[Component]]) -> str:
    """group_id -> [{id, name, unit}] for client-side new-line creation.

    Embedded in a <script> block, so escape the sequences that could break out of
    it (`<`, `>`, `&`, and `/` in `</script>`). Component names are user-supplied,
    so this prevents stored XSS via a crafted name like `</script>...`.
    """
    payload = json.dumps(
        {
            gid: [{"id": c.id, "name": c.name, "unit": c.unit} for c in comps]
            for gid, comps in comps_by_group.items()
        }
    )
    return payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _sections_ctx(session: Session, group_vms, total) -> dict:
    cbg = _comps_by_group(session)
    return {
        "group_vms": group_vms,
        "total": total,
        "comps_by_group": cbg,
        "comps_json": _comps_json(cbg),
        "mass_volume_step": settings.mass_volume_step,
    }


def _parse_lines(component_ids: list[str], amounts: list[str]) -> list[tuple[int, Decimal]]:
    """Zip the parallel form arrays into (component_id, amount) pairs.

    Skips blank/invalid rows so a stray empty picker never breaks a save. A blank
    amount counts as 0; a negative amount is dropped (the input has no native
    min once it accepts commas, so guard it here).
    """
    pairs: list[tuple[int, Decimal]] = []
    for cid, amt in zip(component_ids, amounts, strict=False):
        if not cid:
            continue
        amount = Decimal("0") if not (amt or "").strip() else decimal_hu(amt)
        if amount is None or amount < 0:
            continue
        try:
            pairs.append((int(cid), amount))
        except ValueError:
            continue
    return pairs


# --- list --------------------------------------------------------------------


@router.get("/offers", response_class=HTMLResponse)
def list_offers(
    request: Request,
    q: str = "",
    status: str = "",
    year: str = "",
    session: Session = Depends(get_session),
):
    yr = int(year) if year.strip().isdigit() else None
    # Creation date: entry_date for internal offers, request_date for still-
    # unpriced external drafts. Drives both the newest-first order and the year
    # filter/dropdown (so external drafts are covered before they are priced).
    created = func.coalesce(Offer.entry_date, Offer.request_date)
    # Year in the chef's timezone, not the container's UTC — otherwise an offer
    # created just after Budapest New Year is filed under the previous year.
    created_year = extract("year", func.timezone("Europe/Budapest", created))
    stmt = (
        select(Offer)
        .options(selectinload(Offer.customer))
        .order_by(created.desc().nullslast(), Offer.id.desc())
    )
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.join(Customer).where(
            func.lower(func.coalesce(Offer.theme, "")).like(like)
            | func.lower(func.coalesce(Offer.flavor, "")).like(like)
            | func.lower(Customer.name).like(like)
        )
    if status.strip():
        stmt = stmt.where(Offer.status == status)
    if yr:
        stmt = stmt.where(created_year == yr)
    offers = list(session.scalars(stmt))
    years = list(
        session.scalars(
            select(created_year).where(created_year.is_not(None)).distinct().order_by(created_year)
        )
    )
    ctx = {
        "offers": offers,
        "q": q,
        "status": status,
        "year": yr,
        "statuses": STATUSES,
        "years": [int(y) for y in years],
        "active_nav": "offers",
    }
    tmpl = "offers/_rows.html" if request.headers.get("HX-Request") else "offers/list.html"
    return templates.TemplateResponse(request, tmpl, ctx)


@router.get("/offers/detail/{offer_id:int}", response_class=HTMLResponse)
def offer_detail(offer_id: int, request: Request, session: Session = Depends(get_session)):
    offer = get_or_404(session, Offer, offer_id)
    pairs = offer_svc.load_offer_line_pairs(session, offer_id)
    as_of = offer.entry_date or dt.datetime.now(dt.UTC)
    group_vms, total = offer_svc.build_group_vms(session, pairs, as_of)
    return templates.TemplateResponse(
        request,
        "offers/_detail.html",
        {"o": offer, "group_vms": group_vms, "total": total, "statuses": STATUSES},
    )


# --- form (create/edit) ------------------------------------------------------


def _default_offer_lines(session: Session) -> list[tuple[int, Decimal]]:
    """Lines every new offer starts with: the base-cost service components
    (Munkadíj, Rezsi+amortizáció) at amount 1 each (§3.2)."""
    ids = session.scalars(
        select(Component.id)
        .where(Component.type == "service", Component.active.is_(True))
        .order_by(Component.name)
    )
    return [(cid, Decimal("1")) for cid in ids]


def _form_context(session: Session, offer: Offer | None, pairs, as_of) -> dict:
    group_vms, total = offer_svc.build_group_vms(session, pairs, as_of)
    # Exclude anonymized customers from the picker — but keep the one already on
    # this offer selectable so editing an old offer doesn't lose its customer.
    current = offer.customer_id if offer else None
    customers = list(
        session.scalars(
            select(Customer)
            .where(or_(Customer.anonymized_at.is_(None), Customer.id == current))
            .order_by(Customer.name)
        )
    )
    recipes = list(session.scalars(select(Recipe).order_by(Recipe.name)))
    ctx = _sections_ctx(session, group_vms, total)
    ctx.update(
        {
            "o": offer,
            "customers": customers,
            "recipes": recipes,
            "statuses": STATUSES,
            "active_nav": "offers",
            "as_of": as_of,
        }
    )
    return ctx


@router.get("/offers/new", response_class=HTMLResponse)
def new_offer_form(request: Request, due_date: str = "", session: Session = Depends(get_session)):
    ctx = _form_context(session, None, _default_offer_lines(session), dt.datetime.now(dt.UTC))
    # Pre-fill the deadline when arriving from the calendar (?due_date=YYYY-MM-DD),
    # validated so only a real ISO date reaches the <input value>.
    ctx["preset_due_date"] = _iso_date_or_blank(due_date)
    ctx["return_to"] = return_to(request, "/offers")
    return templates.TemplateResponse(request, "offers/form.html", ctx)


@router.get("/offers/{offer_id:int}/edit", response_class=HTMLResponse)
def edit_offer_form(offer_id: int, request: Request, session: Session = Depends(get_session)):
    offer = get_or_404(session, Offer, offer_id)
    pairs = offer_svc.load_offer_line_pairs(session, offer_id)
    # Unpriced external draft: preview at today's prices — saving will set
    # entry_date to "now", so what she sees is what she gets (§8a).
    as_of = offer.entry_date or dt.datetime.now(dt.UTC)
    ctx = _form_context(session, offer, pairs, as_of)
    ctx["return_to"] = return_to(request, "/offers")
    return templates.TemplateResponse(request, "offers/form.html", ctx)


@router.get("/offers/{offer_id:int}/copy", response_class=HTMLResponse)
def copy_offer_form(offer_id: int, request: Request, session: Session = Depends(get_session)):
    """Open the NEW-offer form pre-filled from an existing offer: its line set and
    flavor (Íz) are copied; theme, due date, customer, and notes are intentionally
    left blank and the status resets to draft (§copy). Posts to POST /offers like
    any new offer — nothing is written until the chef saves."""
    src = get_or_404(session, Offer, offer_id)
    pairs = offer_svc.load_offer_line_pairs(session, offer_id)
    ctx = _form_context(session, None, pairs, dt.datetime.now(dt.UTC))
    ctx["preset_flavor"] = src.flavor or ""
    ctx["return_to"] = return_to(request, "/offers")
    return templates.TemplateResponse(request, "offers/form.html", ctx)


@router.post("/offers/recalc", response_class=HTMLResponse)
def recalc(
    request: Request,
    entry_date: str = Form(""),
    component_id: list[str] = Form(default=[]),
    amount: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Live HTMX recalc of the grouped sections + totals while editing."""
    as_of = _parse_dt(entry_date)
    pairs = _parse_lines(component_id, amount)
    group_vms, total = offer_svc.build_group_vms(session, pairs, as_of)
    return templates.TemplateResponse(
        request, "offers/_sections.html", _sections_ctx(session, group_vms, total)
    )


@router.post("/offers")
def create_offer(
    customer_id: int = Form(...),
    theme: str = Form(""),
    flavor: str = Form(""),
    due_date: str = Form(""),
    status: str = Form("draft"),
    final_price: str = Form(""),
    notes: str = Form(""),
    component_id: list[str] = Form(default=[]),
    amount: list[str] = Form(default=[]),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
):
    offer = Offer(
        customer_id=customer_id,
        theme=theme.strip() or None,
        flavor=flavor.strip() or None,
        due_date=_parse_dt(due_date) if due_date else None,
        status=status,
        final_price=_parse_decimal(final_price),
        notes=notes.strip() or None,
    )
    session.add(offer)
    session.flush()
    offer_svc.save_offer_lines(session, offer, _parse_lines(component_id, amount))
    return see_other_back(session, return_to, "/offers")


@router.post("/offers/{offer_id:int}")
def update_offer(
    offer_id: int,
    customer_id: int = Form(...),
    theme: str = Form(""),
    flavor: str = Form(""),
    due_date: str = Form(""),
    status: str = Form("draft"),
    final_price: str = Form(""),
    notes: str = Form(""),
    component_id: list[str] = Form(default=[]),
    amount: list[str] = Form(default=[]),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
):
    offer = get_or_404(session, Offer, offer_id)
    offer.customer_id = customer_id
    offer.theme = theme.strip() or None
    offer.flavor = flavor.strip() or None
    offer.due_date = _parse_dt(due_date) if due_date else None
    offer.status = status
    offer.final_price = _parse_decimal(final_price)
    offer.notes = notes.strip() or None
    # entry_date is immutable ONCE SET (§3.4). External drafts arrive without
    # one (§8a) — the chef's first save prices the offer as of that moment.
    if offer.entry_date is None:
        offer.entry_date = dt.datetime.now(dt.UTC)
    offer_svc.save_offer_lines(session, offer, _parse_lines(component_id, amount))
    return see_other_back(session, return_to, "/offers")


@router.get("/offers/{offer_id:int}/delete", response_class=HTMLResponse)
def confirm_delete(offer_id: int, request: Request, session: Session = Depends(get_session)):
    offer = get_or_404(session, Offer, offer_id)
    label = f"{offer.customer.name} · {offer.theme or ''}"
    return templates.TemplateResponse(
        request,
        "_confirm.html",
        {
            "action": f"/offers/{offer_id}/delete",
            "title": t("confirm.delete.title"),
            "message": f"„{label}” — {t('offers.title')} + {t('offers.items').lower()}.",
        },
    )


@router.post("/offers/{offer_id:int}/delete")
def delete_offer(offer_id: int, session: Session = Depends(get_session)):
    """Delete offer → cascades to its lines and stock movements (FK ON DELETE CASCADE)."""
    session.delete(get_or_404(session, Offer, offer_id))
    return see_other(session, "/offers")


# --- recipes on the offer form ----------------------------------------------


@router.post("/offers/apply-recipe", response_class=HTMLResponse)
def apply_recipe(
    request: Request,
    recipe_id: int = Form(...),
    entry_date: str = Form(""),
    component_id: list[str] = Form(default=[]),
    amount: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Append a recipe's items to the current form lines (cumulative, §3.5).

    A component already present becomes a SEPARATE line (amounts are NOT merged).
    Returns the re-rendered sections fragment.
    """
    as_of = _parse_dt(entry_date)
    pairs = _parse_lines(component_id, amount)
    items = session.scalars(
        select(RecipeItem).where(RecipeItem.recipe_id == recipe_id).order_by(RecipeItem.id)
    )
    pairs.extend((it.component_id, it.amount) for it in items)
    group_vms, total = offer_svc.build_group_vms(session, pairs, as_of)
    return templates.TemplateResponse(
        request, "offers/_sections.html", _sections_ctx(session, group_vms, total)
    )


@router.post("/offers/save-as-recipe", response_class=HTMLResponse)
def save_as_recipe(
    recipe_name: str = Form(...),
    component_id: list[str] = Form(default=[]),
    amount: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Save the current line set as a reusable Recept, then STAY on the offer form.

    The Alap (base-cost) group is never saved — Munkadíj/Rezsi are added fresh to
    every offer — so those lines are stripped; if nothing else remains, the save
    fails with an inline error. Driven by HTMX: an error returns a message
    fragment (into the dialog), success returns empty + an `HX-Trigger` the form
    JS uses to append the new recipe to the picker and close the dialog.
    """
    base_group_ids = set(session.scalars(select(Group.id).where(Group.name == BASE_GROUP_NAME)))
    pairs = _parse_lines(component_id, amount)
    comp_group: dict[int, int] = (
        dict(
            session.execute(
                select(Component.id, Component.group_id).where(
                    Component.id.in_([cid for cid, _ in pairs])
                )
            )
            .tuples()
            .all()
        )
        if pairs
        else {}
    )
    kept = [(cid, amt) for cid, amt in pairs if comp_group.get(cid) not in base_group_ids]
    if not kept:
        return HTMLResponse(f'<p class="cp-error" role="alert">{t("recipes.save_empty")}</p>')

    recipe = Recipe(name=recipe_name.strip())
    session.add(recipe)
    session.flush()
    for cid, amt in kept:
        session.add(RecipeItem(recipe_id=recipe.id, component_id=cid, amount=amt))
    session.commit()
    trigger = json.dumps({"cpRecipeSaved": {"id": recipe.id, "name": recipe.name}})
    return HTMLResponse("", headers={"HX-Trigger": trigger})


# --- helpers -----------------------------------------------------------------


def _parse_dt(value: str) -> dt.datetime:
    """Parse an ISO datetime or YYYY-MM-DD date; assume UTC only when tz-naive
    (never override an explicit offset)."""
    if not value:
        return dt.datetime.now(dt.UTC)
    parsed: dt.datetime | None = None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = dt.datetime.strptime(value, "%Y-%m-%d")  # noqa: DTZ007 (naive → UTC below)
        except ValueError:
            return dt.datetime.now(dt.UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _parse_decimal(value: str) -> Decimal | None:
    # Final price is entered the same way (accept a Hungarian comma + spaces).
    return decimal_hu(value)


def _iso_date_or_blank(value: str) -> str:
    """A YYYY-MM-DD string echoed back only if it is a real date, else ''."""
    try:
        return dt.date.fromisoformat(value.strip()).isoformat() if value.strip() else ""
    except ValueError:
        return ""
