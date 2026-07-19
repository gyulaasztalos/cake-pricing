"""Small shared helpers for routers."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.models import Base


def see_other(session: Session, url: str) -> RedirectResponse:
    """Commit, then 303-redirect (Post/Redirect/Get).

    The commit MUST happen before the redirect is sent: get_session commits in
    its post-yield teardown, i.e. AFTER the response, so on a slow host the
    browser can follow the 303 and GET the list before the write has landed —
    rendering stale data (read-after-write race). Committing here makes the
    write durable before the client is told to fetch the list.
    """
    session.commit()
    return RedirectResponse(url=url, status_code=303)


def get_or_404[M: Base](session: Session, model: type[M], pk: int) -> M:
    """Fetch a row by primary key or raise 404.

    Centralizes the "row may not exist" guard so handlers can access attributes
    without a None-deref (a bad/stale id returns a clean 404, not a 500).
    """
    obj = session.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {pk} not found")
    return obj
