"""Small shared helpers for routers."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Base


def get_or_404[M: Base](session: Session, model: type[M], pk: int) -> M:
    """Fetch a row by primary key or raise 404.

    Centralizes the "row may not exist" guard so handlers can access attributes
    without a None-deref (a bad/stale id returns a clean 404, not a 500).
    """
    obj = session.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {pk} not found")
    return obj
