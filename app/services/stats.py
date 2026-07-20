"""Offer statistics for the /stats dashboard.

Pure read-model over `offers` (+ the `v_offer_cost` view for calculated cost).
Everything is aggregate — no customer names or other PII is surfaced, so
anonymized customers (§ GDPR) are naturally respected: their offers still count
toward totals, but nothing here can identify them.

Two scopes, driven by one `year` param:
  * year = None  -> all-time KPIs + a per-YEAR series (the yearly view)
  * year = YYYY  -> that year's KPIs + a per-MONTH series (the monthly view)

The offer's "created" moment is COALESCE(entry_date, request_date): entry_date is
the pricing reference date for internal/priced offers, request_date covers
external drafts not yet priced (entry_date still NULL). Year/month are extracted
in Europe/Budapest so an offer made just after local New Year files correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from markupsafe import Markup, escape
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

# Offer statuses that represent a real, won sale (revenue-bearing).
WON = ("accepted", "done")
# Statuses that left the draft stage (were actually sent to a customer).
SENT_OUT = ("sent", "accepted", "rejected", "done")
STATUS_ORDER = ("draft", "sent", "accepted", "rejected", "done")

# Local-time created moment, reused across queries.
_CREATED = "COALESCE(o.entry_date, o.request_date)"
_LOCAL_CREATED = f"timezone('Europe/Budapest', {_CREATED})"


@dataclass(frozen=True)
class Kpis:
    total: int
    won: int
    sent_out: int
    drafts: int
    win_rate: float  # won / sent_out, 0..1
    revenue: Decimal  # SUM(final_price) of won offers
    cost: Decimal  # SUM(calculated_price) of won offers
    margin: Decimal  # revenue - cost
    margin_pct: float  # margin / revenue, 0..1
    avg_offer: Decimal  # revenue / won
    new_customers: int


@dataclass(frozen=True)
class SeriesPoint:
    label: str  # month number ("1".."12") or year ("2025")
    offers: int
    won: int
    revenue: Decimal


@dataclass(frozen=True)
class Stats:
    year: int | None
    years: list[int]
    kpis: Kpis
    series: list[SeriesPoint]
    series_kind: str  # "month" | "year"
    status_counts: list[tuple[str, int]]
    top_flavors: list[tuple[str, int]]
    top_themes: list[tuple[str, int]]
    source_split: dict[str, int] = field(default_factory=dict)


def _year_guard(local_expr: str) -> str:
    """SQL predicate scoping rows to `:year`, or all-time when it is NULL.

    `local_expr` is the local-time timestamp to take the year from. The bind
    param is CAST so Postgres can type it in the all-time (NULL) case — note
    `:year::int` would break SQLAlchemy's bind parsing (it guards `::` casts)."""
    return (
        f"(CAST(:year AS INTEGER) IS NULL "
        f"OR EXTRACT(YEAR FROM {local_expr}) = CAST(:year AS INTEGER))"
    )


def _scalar(session: Session, sql: str, **params: object) -> object:
    return session.execute(text(sql), params).scalar()


def available_years(session: Session) -> list[int]:
    rows = session.execute(
        text(
            f"SELECT DISTINCT EXTRACT(YEAR FROM {_LOCAL_CREATED})::int AS y "  # nosec B608
            f"FROM offers o WHERE {_CREATED} IS NOT NULL ORDER BY y DESC"
        )
    ).scalars()
    return [int(y) for y in rows]


def _kpis(session: Session, year: int | None) -> Kpis:
    row = session.execute(
        text(
            f"""
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE o.status IN :won) AS won,
              COUNT(*) FILTER (WHERE o.status IN :sent_out) AS sent_out,
              COUNT(*) FILTER (WHERE o.status = 'draft') AS drafts,
              COALESCE(SUM(o.final_price) FILTER (WHERE o.status IN :won), 0) AS revenue,
              COALESCE(SUM(vc.calculated_price) FILTER (WHERE o.status IN :won), 0) AS cost
            FROM offers o
            JOIN v_offer_cost vc ON vc.offer_id = o.id
            WHERE {_year_guard(_LOCAL_CREATED)}
            """  # nosec B608
        ).bindparams(
            bindparam("won", expanding=True),
            bindparam("sent_out", expanding=True),
        ),
        {"year": year, "won": list(WON), "sent_out": list(SENT_OUT)},
    ).one()

    cust_local = "timezone('Europe/Budapest', c.entry_date)"
    cust_count = _scalar(
        session,
        f"SELECT COUNT(*) FROM customers c WHERE {_year_guard(cust_local)}",  # nosec B608
        year=year,
    )
    new_customers = int(cust_count) if isinstance(cust_count, int) else 0

    revenue = Decimal(row.revenue)
    cost = Decimal(row.cost)
    margin = revenue - cost
    won = int(row.won)
    sent_out = int(row.sent_out)
    return Kpis(
        total=int(row.total),
        won=won,
        sent_out=sent_out,
        drafts=int(row.drafts),
        win_rate=(won / sent_out) if sent_out else 0.0,
        revenue=revenue,
        cost=cost,
        margin=margin,
        margin_pct=(float(margin) / float(revenue)) if revenue else 0.0,
        avg_offer=(revenue / won) if won else Decimal(0),
        new_customers=new_customers,
    )


def _series(session: Session, year: int | None) -> tuple[list[SeriesPoint], str]:
    if year is None:
        bucket = f"EXTRACT(YEAR FROM {_LOCAL_CREATED})::int"
        where = f"WHERE {_CREATED} IS NOT NULL"
        kind = "year"
    else:
        bucket = f"EXTRACT(MONTH FROM {_LOCAL_CREATED})::int"
        where = f"WHERE EXTRACT(YEAR FROM {_LOCAL_CREATED}) = :year"
        kind = "month"
    rows = session.execute(
        text(
            f"""
            SELECT {bucket} AS b,
                   COUNT(*) AS offers,
                   COUNT(*) FILTER (WHERE o.status IN :won) AS won,
                   COALESCE(SUM(o.final_price) FILTER (WHERE o.status IN :won), 0) AS revenue
            FROM offers o
            {where}
            GROUP BY b ORDER BY b
            """  # nosec B608
        ).bindparams(bindparam("won", expanding=True)),
        {"year": year, "won": list(WON)},
    ).all()
    by_bucket = {
        int(r.b): SeriesPoint(str(int(r.b)), int(r.offers), int(r.won), Decimal(r.revenue))
        for r in rows
    }
    if year is not None:
        # Dense 12-month axis so gaps read as zero, not as missing bars.
        return [by_bucket.get(m, SeriesPoint(str(m), 0, 0, Decimal(0))) for m in range(1, 13)], kind
    return list(by_bucket.values()), kind


def _status_counts(session: Session, year: int | None) -> list[tuple[str, int]]:
    rows = session.execute(
        text(
            f"SELECT o.status AS s, COUNT(*) AS c FROM offers o "  # nosec B608
            f"WHERE {_year_guard(_LOCAL_CREATED)} GROUP BY o.status"
        ),
        {"year": year},
    ).all()
    counts = {r.s: int(r.c) for r in rows}
    return [(s, counts.get(s, 0)) for s in STATUS_ORDER]


def _top(session: Session, column: str, year: int | None, limit: int = 8) -> list[tuple[str, int]]:
    # column is a fixed identifier ('flavor'|'theme'), never user input.
    rows = session.execute(
        text(
            f"SELECT NULLIF(TRIM(o.{column}), '') AS k, COUNT(*) AS c FROM offers o "  # nosec B608
            f"WHERE NULLIF(TRIM(o.{column}), '') IS NOT NULL "
            f"AND {_year_guard(_LOCAL_CREATED)} "
            f"GROUP BY k ORDER BY c DESC, k ASC LIMIT :lim"
        ),
        {"year": year, "lim": limit},
    ).all()
    return [(str(r.k), int(r.c)) for r in rows]


def _source_split(session: Session, year: int | None) -> dict[str, int]:
    rows = session.execute(
        text(
            f"SELECT o.source AS src, COUNT(*) AS c FROM offers o "  # nosec B608
            f"WHERE {_year_guard(_LOCAL_CREATED)} GROUP BY o.source"
        ),
        {"year": year},
    ).all()
    out = {"internal": 0, "external": 0}
    for r in rows:
        out[r.src] = int(r.c)
    return out


def collect(session: Session, year: int | None) -> Stats:
    series, kind = _series(session, year)
    return Stats(
        year=year,
        years=available_years(session),
        kpis=_kpis(session, year),
        series=series,
        series_kind=kind,
        status_counts=_status_counts(session, year),
        top_flavors=_top(session, "flavor", year),
        top_themes=_top(session, "theme", year),
        source_split=_source_split(session, year),
    )


# --- SVG bar chart -----------------------------------------------------------
# Server-rendered, dependency-free, and CSS-themed (fills come from app.css so
# the chart follows the palette). Labels are numeric (months/years) — escaped
# anyway as defence-in-depth. Bars carry a <title> for the exact value on hover.

_MONTHS_HU = [
    "",
    "Jan",
    "Feb",
    "Már",
    "Ápr",
    "Máj",
    "Jún",
    "Júl",
    "Aug",
    "Sze",
    "Okt",
    "Nov",
    "Dec",
]


def _compact(value: float) -> str:
    """Short axis label for money: 1 250 000 -> '1,3M', 42 000 -> '42e'."""
    v = float(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".", ",")
    if v >= 1_000:
        return f"{round(v / 1_000)}e"
    return str(int(v))


def bar_chart(
    labels: list[str],
    values: list[float],
    *,
    highlights: list[float] | None = None,
    money: bool = False,
    height: int = 180,
) -> Markup:
    """A labelled bar chart as inline SVG markup.

    `highlights` (optional, same length) draws a darker sub-bar inside each bar —
    used to show the "won" portion of the offer counts. `money` switches the
    on-bar labels to compact HUF.
    """
    n = len(values)
    if n == 0:
        return Markup('<p class="cp-muted">—</p>')
    slot = 48
    pad_l, pad_r, pad_t, pad_b = 6, 6, 22, 26
    plot_h = height - pad_t - pad_b
    width = pad_l + pad_r + slot * n
    bar_w = slot * 0.56
    maxv = max(values) or 1.0

    parts: list[str] = [
        f'<svg class="cp-chart" viewBox="0 0 {width} {height}" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        cx = pad_l + slot * i + slot / 2
        x = cx - bar_w / 2
        h = (value / maxv) * plot_h if maxv else 0
        y = pad_t + (plot_h - h)
        disp = _compact(value) if money else str(int(value))
        title = _compact(value) if money else disp
        parts.append(
            f'<rect class="cp-bar" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" rx="2"><title>{escape(label)}: {escape(title)}</title></rect>'
        )
        if highlights is not None:
            hv = highlights[i]
            hh = (hv / maxv) * plot_h if maxv else 0
            parts.append(
                f'<rect class="cp-bar cp-bar--hi" x="{x:.1f}" y="{pad_t + (plot_h - hh):.1f}" '
                f'width="{bar_w:.1f}" height="{hh:.1f}" rx="2"/>'
            )
        if value:
            parts.append(
                f'<text class="cp-bar-val" x="{cx:.1f}" y="{y - 4:.1f}" '
                f'text-anchor="middle">{escape(disp)}</text>'
            )
        parts.append(
            f'<text class="cp-bar-lbl" x="{cx:.1f}" y="{height - 8}" '
            f'text-anchor="middle">{escape(label)}</text>'
        )
    parts.append("</svg>")
    # Safe: every dynamic value (labels, values) is escape()-d above; the rest is
    # static SVG this function emits. Nothing here is un-escaped user input.
    return Markup("".join(parts))  # noqa: S704  # nosec B704


def month_labels(points: list[SeriesPoint]) -> list[str]:
    """Hungarian month abbreviations for a 12-point monthly series."""
    return [_MONTHS_HU[int(p.label)] for p in points]
