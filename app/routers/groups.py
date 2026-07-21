"""Csoportok (Groups) — create + edit name/sort_order, NO delete (§3.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Group
from app.routers._helpers import get_or_404, return_to, see_other_back
from app.templating import templates

router = APIRouter()


def _all_groups(session: Session) -> list[Group]:
    return list(session.scalars(select(Group).order_by(Group.name)))


@router.get("/groups", response_class=HTMLResponse)
def list_groups(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "groups/list.html", {"groups": _all_groups(session), "active_nav": "groups"}
    )


@router.get("/groups/new", response_class=HTMLResponse)
def new_group_form(request: Request):
    return templates.TemplateResponse(
        request, "groups/form.html", {"group": None, "return_to": return_to(request, "/groups")}
    )


@router.get("/groups/{group_id:int}/edit", response_class=HTMLResponse)
def edit_group_form(group_id: int, request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "groups/form.html",
        {"group": get_or_404(session, Group, group_id), "return_to": return_to(request, "/groups")},
    )


@router.post("/groups")
def create_group(
    name: str = Form(...), return_to: str = Form(""), session: Session = Depends(get_session)
):
    session.add(Group(name=name.strip()))
    return see_other_back(session, return_to, "/groups")


@router.post("/groups/{group_id:int}")
def update_group(
    group_id: int,
    name: str = Form(...),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
):
    group = get_or_404(session, Group, group_id)
    group.name = name.strip()
    return see_other_back(session, return_to, "/groups")
