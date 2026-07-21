"""Small shared helpers for routers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.models import Base


def decimal_hu(raw: str | None) -> Decimal | None:
    """Parse a number the chef may type in Hungarian notation — decimal COMMA
    and space thousands separators, e.g. '2,5' or '45 000,50'. Returns None on
    blank or garbage. Shared by the offer and recipe forms so the comma works
    on both the live recalc and the real form submit, regardless of browser
    locale. str.split() drops every run of Unicode whitespace (regular AND
    non-breaking space, which the huf display uses for thousands)."""
    if raw is None:
        return None
    cleaned = "".join(raw.split()).replace(",", ".")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


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


def _safe_path(raw: str | None, same_host: str | None) -> str | None:
    """Reduce `raw` (a relative path, or a same-origin absolute URL) to a safe
    in-app `path[?query]`, or None. Guards against open redirects: a foreign
    host, a non-http scheme, a protocol-relative `//`, a `\\`, or anything not
    rooted at a single `/` is rejected. Pass same_host=None to accept relative
    paths only (used for the value posted back from a form)."""
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.netloc and parts.netloc != same_host:
        return None
    path = parts.path
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return None
    return f"{path}?{parts.query}" if parts.query else path


def return_to(request: Request, fallback: str) -> str:
    """Where a form's Save/Cancel should go back to: an explicit `?next=`, else
    the page that linked here (Referer), else `fallback`. Same-origin only, so
    it can never become an open redirect. Threaded through the form as a hidden
    field so it survives the POST."""
    host = request.url.netloc
    return (
        _safe_path(request.query_params.get("next"), host)
        or _safe_path(request.headers.get("referer"), host)
        or fallback
    )


def see_other_back(session: Session, posted: str, fallback: str) -> RedirectResponse:
    """Commit + 303 to the form's posted return_to (re-validated), else fallback.
    The posted value is always a relative path (produced by return_to())."""
    return see_other(session, _safe_path(posted, None) or fallback)


def get_or_404[M: Base](session: Session, model: type[M], pk: int) -> M:
    """Fetch a row by primary key or raise 404.

    Centralizes the "row may not exist" guard so handlers can access attributes
    without a None-deref (a bad/stale id returns a clean 404, not a 500).
    """
    obj = session.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {pk} not found")
    return obj
