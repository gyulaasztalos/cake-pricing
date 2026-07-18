"""Customer-intake API (§8a): the ONE write path for the public cake-order app.

Deliberately narrow: a single token-authed endpoint that creates a customer
(deduplicated by e-mail) and an EXTERNAL DRAFT offer with request_date but NO
entry_date — the pricing reference date is set when the chef first saves the
offer. It can read nothing and touch nothing else.

Reachability: cake-order calls the ClusterIP service directly (bypasses the
Authentik ingress); requests arriving via the ingress hit forward-auth first.
The bearer token is required either way.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, null, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import Customer, Offer

logger = logging.getLogger(__name__)
router = APIRouter()


class IntakeOffer(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=64)
    due_date: dt.date
    theme: str | None = Field(default=None, max_length=200)
    flavor: str | None = Field(default=None, max_length=200)
    portions: int | None = Field(default=None, ge=1, le=500)
    description: str = Field(min_length=1, max_length=4000)
    locale: str = Field(default="hu", max_length=5)
    request_date: dt.datetime


def _require_token(authorization: str | None = Header(None)) -> None:
    if not settings.intake_token:
        raise HTTPException(status_code=503, detail="intake disabled")
    expected = f"Bearer {settings.intake_token}"
    if not (authorization and hmac.compare_digest(authorization, expected)):
        raise HTTPException(status_code=401, detail="invalid token")


def _find_or_create_customer(session: Session, payload: IntakeOffer) -> Customer:
    """Reuse the customer whose contact contains this e-mail (case-insensitive),
    skipping anonymized rows; otherwise create one."""
    existing = session.scalars(
        select(Customer)
        .where(
            Customer.anonymized_at.is_(None),
            func.lower(func.coalesce(Customer.contact, "")).contains(payload.email.lower()),
        )
        .order_by(Customer.id)
    ).first()
    if existing:
        return existing
    contact = payload.email if not payload.phone else f"{payload.email} · {payload.phone}"
    customer = Customer(name=payload.name, contact=contact)
    session.add(customer)
    session.flush()
    return customer


@router.post("/api/intake/offers", status_code=201, dependencies=[Depends(_require_token)])
def create_intake_offer(
    payload: IntakeOffer, session: Session = Depends(get_session)
) -> dict[str, int]:
    customer = _find_or_create_customer(session, payload)
    notes_parts = [payload.description]
    if payload.locale != "hu":
        notes_parts.append(f"[{payload.locale}]")
    offer = Offer(
        customer_id=customer.id,
        due_date=dt.datetime.combine(payload.due_date, dt.time(), tzinfo=dt.UTC),
        theme=payload.theme,
        flavor=payload.flavor,
        portions=payload.portions,
        status="draft",
        notes="\n".join(notes_parts),
        source="external",
        request_date=payload.request_date,
        # Explicit SQL NULL: with a plain None, SQLAlchemy would omit the
        # column and the server_default (now()) would price the draft (§8a).
        entry_date=null(),
    )
    session.add(offer)
    session.flush()
    logger.info("intake offer %s for customer %s", offer.id, customer.id)
    return {"offer_id": offer.id, "customer_id": customer.id}
