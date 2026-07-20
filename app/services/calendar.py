"""Published .ics calendar feed of offer deadlines (PLANNING §Calendar).

One all-day VEVENT per offer that has a `due_date` (Határidő), so the chef can
subscribe in Apple Calendar and see the production schedule on her phone/Mac.
Read-only: no Apple credentials are stored anywhere.

Deadlines are a *date*, not a moment, so events are emitted as all-day VEVENTs
keyed on the due date **in Europe/Budapest** (§3.0 stores timestamps in UTC; a
23:00 UTC deadline is already "tomorrow" locally and must not slip a day).

Rejected offers are excluded — they are not work to be done.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.i18n import t
from app.models import Offer

# Offers in this status are not upcoming work and stay out of the calendar.
EXCLUDED_STATUSES = ("rejected",)

_LOCAL_TZ = ZoneInfo("Europe/Budapest")
# Stable UID domain — keeps a VEVENT identifiable across refreshes so calendar
# clients update rather than duplicate an event.
_UID_DOMAIN = "cake-pricing.local"
_PRODID = "-//Anita Tortai//cake-pricing//HU"


def upcoming_offers(session: Session) -> list[Offer]:
    """Offers with a deadline, newest deadline last, excluding rejected ones."""
    return list(
        session.scalars(
            select(Offer)
            .options(selectinload(Offer.customer))
            .where(Offer.due_date.is_not(None), Offer.status.not_in(EXCLUDED_STATUSES))
            .order_by(Offer.due_date)
        )
    )


# --- month view ---------------------------------------------------------------


@dataclass(frozen=True)
class CalendarDay:
    """One row of the month view."""

    date: dt.date
    offers: list[Offer]
    is_weekend: bool
    is_today: bool
    is_past: bool
    starts_week: bool  # Monday — the template draws a week divider above it


def local_today() -> dt.date:
    """Today in the chef's timezone (not the container's UTC)."""
    return dt.datetime.now(_LOCAL_TZ).date()


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Month `delta` months away, rolling the year over correctly."""
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def month_days(session: Session, year: int, month: int) -> list[CalendarDay]:
    """Every day of the month with its offers (rejected excluded).

    The DB stores timestamps in UTC, so the month window is computed from local
    midnight boundaries and each offer is bucketed by its LOCAL date — otherwise
    a late-evening deadline would land on the wrong day.
    """
    first = dt.date(year, month, 1)
    last = dt.date(year, month, _calendar.monthrange(year, month)[1])
    start_utc = dt.datetime.combine(first, dt.time(), tzinfo=_LOCAL_TZ).astimezone(dt.UTC)
    end_utc = dt.datetime.combine(
        last + dt.timedelta(days=1), dt.time(), tzinfo=_LOCAL_TZ
    ).astimezone(dt.UTC)

    rows = session.scalars(
        select(Offer)
        .options(selectinload(Offer.customer))
        .where(
            Offer.due_date.is_not(None),
            Offer.due_date >= start_utc,
            Offer.due_date < end_utc,
            Offer.status.not_in(EXCLUDED_STATUSES),
        )
        .order_by(Offer.due_date, Offer.id)
    )
    by_day: dict[dt.date, list[Offer]] = {}
    for offer in rows:
        # The query already filters these out; the guard keeps the type narrow
        # without an assert (asserts vanish under `python -O`).
        if offer.due_date is None:
            continue
        by_day.setdefault(_local_date(offer.due_date), []).append(offer)

    today = local_today()
    days: list[CalendarDay] = []
    for offset in range((last - first).days + 1):
        day = first + dt.timedelta(days=offset)
        days.append(
            CalendarDay(
                date=day,
                offers=by_day.get(day, []),
                is_weekend=day.weekday() >= 5,
                is_today=day == today,
                is_past=day < today,
                starts_week=day.weekday() == 0,
            )
        )
    return days


def _local_date(value: dt.datetime) -> dt.date:
    """The deadline's calendar day in the chef's timezone."""
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(_LOCAL_TZ).date()


def _escape(value: str) -> str:
    """RFC 5545 TEXT escaping: backslash, semicolon, comma, and newlines."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to 75 **octets** (RFC 5545 §3.1).

    Folding counts octets, not characters — Hungarian accents are 2 bytes in
    UTF-8, so a naive character-based fold would emit over-long lines. Split on
    encoded bytes and never inside a multi-byte sequence.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks: list[bytes] = []
    start = 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Back off so we never cut a multi-byte UTF-8 character in half.
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[start:end])
        start = end
        limit = 74  # continuation lines start with a space
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)


def _money(value: Decimal | None) -> str:
    """Whole-forint price as shown to the customer, or a dash when unquoted."""
    if value is None:
        return "—"
    return f"{int(round(float(value))):,}".replace(",", " ") + " Ft"


def _summary(offer: Offer) -> str:
    """What shows on the calendar grid: who it's for, and what it is."""
    customer = offer.customer.name if offer.customer else "?"
    theme = (offer.theme or "").strip()
    return f"{customer} — {theme}" if theme else customer


def _description(offer: Offer) -> str:
    """The detail lines, one label per line (rendered in the event body)."""
    customer = offer.customer.name if offer.customer else "?"
    lines = [
        f"{t('customers.title')}: {customer}",
        f"{t('offers.theme')}: {(offer.theme or '').strip() or '—'}",
        f"{t('offers.flavor')}: {(offer.flavor or '').strip() or '—'}",
        f"{t('offers.status')}: {t('offers.status.' + offer.status)}",
        f"{t('offers.final_price')}: {_money(offer.final_price)}",
    ]
    return "\n".join(lines)


def build_ics(session: Session, now: dt.datetime | None = None) -> str:
    """The whole calendar as an iCalendar document (CRLF-terminated)."""
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(t('calendar.feed_name'))}",
        "X-WR-TIMEZONE:Europe/Budapest",
    ]
    for offer in upcoming_offers(session):
        # Same as month_days(): guard instead of assert (see note there).
        if offer.due_date is None:
            continue
        day = _local_date(offer.due_date)
        lines += [
            "BEGIN:VEVENT",
            f"UID:offer-{offer.id}@{_UID_DOMAIN}",
            f"DTSTAMP:{stamp}",
            # All-day event: DTEND is exclusive, so it is the following day.
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(_summary(offer))}",
            f"DESCRIPTION:{_escape(_description(offer))}",
            f"STATUS:{'CANCELLED' if offer.status == 'rejected' else 'CONFIRMED'}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(f"{_fold(line)}\r\n" for line in lines)
