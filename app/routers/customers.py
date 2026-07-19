"""Ügyfelek (Customers) — CRUD + anonymize (no hard delete) + their-offers view (§3.7)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.i18n import t
from app.models import Customer, Offer
from app.routers._helpers import get_or_404, see_other
from app.templating import templates

router = APIRouter()


@router.get("/customers", response_class=HTMLResponse)
def list_customers(request: Request, q: str = "", session: Session = Depends(get_session)):
    stmt = select(Customer)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Customer.name).like(like)
            | func.lower(func.coalesce(Customer.contact, "")).like(like)
        )
    stmt = stmt.order_by(Customer.name)
    customers = list(session.scalars(stmt))
    counts: dict[int, int] = dict(
        session.execute(select(Offer.customer_id, func.count(Offer.id)).group_by(Offer.customer_id))
        .tuples()
        .all()
    )
    ctx = {"customers": customers, "counts": counts, "q": q, "active_nav": "customers"}
    tmpl = "customers/_rows.html" if request.headers.get("HX-Request") else "customers/list.html"
    return templates.TemplateResponse(request, tmpl, ctx)


@router.get("/customers/detail/{customer_id:int}", response_class=HTMLResponse)
def customer_detail(customer_id: int, request: Request, session: Session = Depends(get_session)):
    customer = get_or_404(session, Customer, customer_id)
    # Newest first by creation date (entry_date, or request_date for unpriced
    # external drafts), matching the main offers list ordering.
    created = func.coalesce(Offer.entry_date, Offer.request_date)
    offers = list(
        session.scalars(
            select(Offer)
            .where(Offer.customer_id == customer_id)
            .order_by(created.desc().nullslast(), Offer.id.desc())
        )
    )
    return templates.TemplateResponse(
        request, "customers/_detail.html", {"c": customer, "offers": offers}
    )


@router.get("/customers/new", response_class=HTMLResponse)
def new_customer_form(request: Request):
    return templates.TemplateResponse(request, "customers/form.html", {"c": None})


@router.get("/customers/quick-new", response_class=HTMLResponse)
def quick_new_form(request: Request):
    """Inline customer-create dialog for the offer form (§UI §7)."""
    return templates.TemplateResponse(request, "customers/_quick_new.html", {})


@router.post("/customers/quick-new", response_class=HTMLResponse)
def quick_new_create(
    request: Request,
    name: str = Form(...),
    contact: str = Form(""),
    session: Session = Depends(get_session),
):
    c = Customer(name=name.strip(), contact=contact.strip() or None)
    session.add(c)
    # Commit before returning the id — the offer form selects and may submit
    # against it right away, so it must be durable now (cf. _helpers.see_other).
    session.commit()
    return templates.TemplateResponse(
        request, "customers/_quick_created.html", {"id": c.id, "name": c.name}
    )


@router.get("/customers/{customer_id:int}/edit", response_class=HTMLResponse)
def edit_customer_form(customer_id: int, request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "customers/form.html", {"c": get_or_404(session, Customer, customer_id)}
    )


@router.get("/customers/{customer_id:int}/delete", response_class=HTMLResponse)
def confirm_anonymize(customer_id: int, request: Request, session: Session = Depends(get_session)):
    customer = get_or_404(session, Customer, customer_id)
    return templates.TemplateResponse(
        request,
        "_confirm.html",
        {
            "action": f"/customers/{customer_id}/anonymize",
            "title": t("customers.anonymize"),
            "message": f"„{customer.name}” — {t('customers.confirm_anonymize')}",
        },
    )


@router.post("/customers")
def create_customer(
    name: str = Form(...),
    contact: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    session.add(
        Customer(name=name.strip(), contact=contact.strip() or None, notes=notes.strip() or None)
    )
    return see_other(session, "/customers")


@router.post("/customers/{customer_id:int}")
def update_customer(
    customer_id: int,
    name: str = Form(...),
    contact: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    c = get_or_404(session, Customer, customer_id)
    c.name = name.strip()
    c.contact = contact.strip() or None
    c.notes = notes.strip() or None
    return see_other(session, "/customers")


@router.post("/customers/{customer_id:int}/anonymize")
def anonymize_customer(customer_id: int, session: Session = Depends(get_session)):
    """Scrub PII, keep the row + its offers (§3.7). No hard delete."""
    c = get_or_404(session, Customer, customer_id)
    c.name = f"Anon{c.id}"  # stable, unique, PII-free label
    c.contact = None
    c.notes = None
    c.anonymized_at = dt.datetime.now(dt.UTC)
    return see_other(session, "/customers")
