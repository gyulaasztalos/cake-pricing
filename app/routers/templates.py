"""Sablonok (Templates / Recipes) — list, edit, delete (§3.5).

A template = a saved line set; size is encoded in the name. Building/editing the
line set reuses the same grouped-line editor as the offer form.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.i18n import t
from app.models import Component, Recipe, RecipeItem
from app.routers._helpers import decimal_hu, get_or_404, return_to, see_other, see_other_back
from app.templating import templates as tmpl

router = APIRouter()


@router.get("/templates", response_class=HTMLResponse)
def list_templates(request: Request, q: str = "", session: Session = Depends(get_session)):
    stmt = select(Recipe)
    if q:
        stmt = stmt.where(func.lower(Recipe.name).like(f"%{q.lower()}%"))
    stmt = stmt.order_by(Recipe.name)
    recipes = list(session.scalars(stmt))
    counts: dict[int, int] = dict(
        session.execute(
            select(RecipeItem.recipe_id, func.count(RecipeItem.id)).group_by(RecipeItem.recipe_id)
        )
        .tuples()
        .all()
    )
    ctx = {"recipes": recipes, "counts": counts, "q": q, "active_nav": "templates"}
    name = "templates/_rows.html" if request.headers.get("HX-Request") else "templates/list.html"
    return tmpl.TemplateResponse(request, name, ctx)


def _load_recipe(session: Session, recipe_id: int) -> Recipe:
    recipe = session.scalars(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(selectinload(Recipe.items).selectinload(RecipeItem.component))
    ).first()
    if recipe is None:
        raise HTTPException(status_code=404, detail=f"Recipe {recipe_id} not found")
    return recipe


@router.get("/templates/detail/{recipe_id:int}", response_class=HTMLResponse)
def template_detail(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = _load_recipe(session, recipe_id)
    return tmpl.TemplateResponse(request, "templates/_detail.html", {"r": recipe})


@router.get("/templates/{recipe_id:int}/edit", response_class=HTMLResponse)
def edit_template_form(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = _load_recipe(session, recipe_id)
    components = list(
        session.scalars(
            select(Component).where(Component.active.is_(True)).order_by(Component.name)
        )
    )
    return tmpl.TemplateResponse(
        request,
        "templates/form.html",
        {"r": recipe, "components": components, "return_to": return_to(request, "/templates")},
    )


@router.post("/templates/{recipe_id:int}")
def update_template(
    recipe_id: int,
    name: str = Form(...),
    notes: str = Form(""),
    component_id: list[str] = Form(default=[]),
    amount: list[str] = Form(default=[]),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
):
    recipe = get_or_404(session, Recipe, recipe_id)
    recipe.name = name.strip()
    recipe.notes = notes.strip() or None
    recipe.items.clear()
    session.flush()
    for cid, amt in zip(component_id, amount, strict=False):
        if not cid:
            continue
        value = decimal_hu(amt) if (amt or "").strip() else None
        if value is None or value < 0:
            continue
        try:
            recipe.items.append(RecipeItem(component_id=int(cid), amount=value))
        except ValueError:
            continue
    return see_other_back(session, return_to, "/templates")


@router.get("/templates/{recipe_id:int}/delete", response_class=HTMLResponse)
def confirm_delete(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = get_or_404(session, Recipe, recipe_id)
    return tmpl.TemplateResponse(
        request,
        "_confirm.html",
        {
            "action": f"/templates/{recipe_id}/delete",
            "title": t("confirm.delete.title"),
            "message": f"„{recipe.name}”",
        },
    )


@router.post("/templates/{recipe_id:int}/delete")
def delete_template(recipe_id: int, session: Session = Depends(get_session)):
    session.delete(get_or_404(session, Recipe, recipe_id))
    return see_other(session, "/templates")
