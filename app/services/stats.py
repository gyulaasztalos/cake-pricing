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

# Offer statuses that represent a real, won sale (revenue-bearing). 'deposit'
# (Előlegezve) is included so an accepted offer that receives a deposit does not
# fall out of revenue.
WON = ("accepted", "deposit", "done", "cancelled")
# Money is only counted for FINISHED work. An accepted or part-paid (deposit)
# offer is WON but not yet earned, and counting it would also set partial revenue
# against a full cost. Kept separate from WON, which still answers "did I win the
# offer?" for the win rate.
# A cancellation is money too when a deposit was kept — but only the amount
# actually recorded in Fizetve, never the quoted price (see _REVENUE).
EARNED = ("done", "cancelled")
# Statuses that left the draft stage (were actually sent to a customer).
# 'cancelled' belongs here as well as in WON — win_rate is won/sent_out, so a
# status counted as won but not as sent would push the rate above 100%.
SENT_OUT = ("sent", "accepted", "deposit", "rejected", "done", "cancelled")
STATUS_ORDER = (
    "draft",
    "sent",
    "accepted",
    "deposit",
    "rejected",
    "done",
    "cancelled",
)
# Revenue prefers the amount actually paid (Fizetve), falling back to the quoted
# final price when nothing is recorded yet.
# For a CANCELLED offer the quoted price was never collected, so the usual
# fallback must not apply: only what is actually recorded in Fizetve counts, and
# nothing at all if the deposit was refunded.
_REVENUE = (
    "CASE WHEN o.status = 'cancelled' THEN COALESCE(o.paid, 0) "
    "ELSE COALESCE(o.paid, o.final_price) END"
)

# The base-cost group (Munkadíj, Rezsi) — same constant the offer form uses.
BASE_GROUP_NAME = "Alap"

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
    revenue: Decimal  # SUM(paid, falling back to final_price) of FINISHED offers
    avg_offer: Decimal  # revenue / number of finished offers
    new_customers: int


@dataclass(frozen=True)
class SeriesPoint:
    label: str  # month number ("1".."12") or year ("2025")
    offers: int
    won: int
    revenue: Decimal


@dataclass(frozen=True)
class PortionStat:
    """One slice-count bucket: how many offers, and what a slice went for."""

    portions: int
    offers: int
    avg_per_portion: Decimal  # mean final_price/portions across those offers


@dataclass(frozen=True)
class DoneSplit:
    """Where the money from FINISHED (Kész) work went.

    Scoped to `done` on purpose: only a completed, fully-paid job has earned its
    Munkadíj and its tip, and it sidesteps the part-payment distortion a deposit
    would introduce (deposit revenue against full cost).
    """

    base_rows: list[tuple[str, Decimal]]  # each Alap-group component, by name
    tip: Decimal  # Σ (paid − final_price) where positive
    shortfall: Decimal  # Σ (final_price − paid) where positive — money never collected
    cancellation: Decimal  # Σ paid on CANCELLED offers — kept deposits
    materials: Decimal  # everything outside the Alap group


@dataclass(frozen=True)
class BizProfit:
    """Üzleti profit over FINISHED (Kész/done) offers.

    Profit is quote − computed cost (final_price − calculated_price); it ignores
    `paid` on purpose, so it measures the pricing decision, not what was actually
    collected. Done-only for the same reason as DoneSplit (no deposit distortion).
    """

    count: int  # done offers with a final price
    total: Decimal  # Σ (final_price − calculated_price)
    avg: Decimal  # mean (final_price − calculated_price) per offer
    avg_pct: float  # mean per-offer (final_price / calculated_price − 1), 0..1


@dataclass(frozen=True)
class Stats:
    year: int | None
    years: list[int]
    kpis: Kpis
    series: list[SeriesPoint]
    series_kind: str  # "month" | "year"
    status_counts: list[tuple[str, int]]
    top_sponges: list[tuple[str, int]]
    top_flavors: list[tuple[str, int]]
    top_themes: list[tuple[str, int]]
    by_portions: list[PortionStat] = field(default_factory=list)
    avg_per_portion: Decimal | None = None  # overall, across all priced offers
    done_split: DoneSplit | None = None
    biz_profit: BizProfit | None = None
    source_split: dict[str, int] = field(default_factory=dict)

    @property
    def done_total(self) -> Decimal:
        """Bottom line of the Kész breakdown: Alap components + borravaló +
        anyagköltség + üzleti profit.

        Equals `kpis.revenue` by construction — every forint a finished offer
        brought in is either a cost line, the tip, or profit. Shown as the
        "Összesen" row so the identity is visible, and asserted by
        `test_the_done_breakdown_reconciles_to_revenue`.
        """
        if self.done_split is None:
            return Decimal(0)
        base = sum((v for _, v in self.done_split.base_rows), Decimal(0))
        profit = self.biz_profit.total if self.biz_profit else Decimal(0)
        d = self.done_split
        return base + d.materials + profit + d.tip - d.shortfall + d.cancellation


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


def _money(session: Session, sql: str, **params: object) -> Decimal:
    """A money aggregate as a Decimal (the queries all COALESCE to 0)."""
    value = _scalar(session, sql, **params)
    return Decimal(str(value)) if value is not None else Decimal(0)


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
              COUNT(*) FILTER (WHERE o.status IN :earned) AS earned,
              COALESCE(SUM({_REVENUE}) FILTER (WHERE o.status IN :earned), 0) AS revenue
            FROM offers o
            WHERE {_year_guard(_LOCAL_CREATED)}
            """  # nosec B608
        ).bindparams(
            bindparam("won", expanding=True),
            bindparam("sent_out", expanding=True),
            bindparam("earned", expanding=True),
        ),
        {
            "year": year,
            "won": list(WON),
            "sent_out": list(SENT_OUT),
            "earned": list(EARNED),
        },
    ).one()

    cust_local = "timezone('Europe/Budapest', c.entry_date)"
    cust_count = _scalar(
        session,
        f"SELECT COUNT(*) FROM customers c WHERE {_year_guard(cust_local)}",  # nosec B608
        year=year,
    )
    new_customers = int(cust_count) if isinstance(cust_count, int) else 0

    revenue = Decimal(row.revenue)
    won = int(row.won)
    sent_out = int(row.sent_out)
    return Kpis(
        total=int(row.total),
        won=won,
        sent_out=sent_out,
        drafts=int(row.drafts),
        win_rate=(won / sent_out) if sent_out else 0.0,
        revenue=revenue,
        # Averaged over the SAME offers the revenue came from, not over `won`.
        avg_offer=(revenue / int(row.earned)) if int(row.earned) else Decimal(0),
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
                   COALESCE(SUM({_REVENUE}) FILTER (WHERE o.status IN :earned), 0) AS revenue
            FROM offers o
            {where}
            GROUP BY b ORDER BY b
            """  # nosec B608
        ).bindparams(bindparam("won", expanding=True), bindparam("earned", expanding=True)),
        {"year": year, "won": list(WON), "earned": list(EARNED)},
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
    # column is a fixed identifier ('flavor'|'theme'|'sponge'), never user input.
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


def _by_portions(session: Session, year: int | None, limit: int = 8) -> list[PortionStat]:
    """Per slice-count: offer count and the average price per slice.

    Only offers that have BOTH a slice count and a final price can yield a
    per-slice figure, so the average is taken over those; the offer count uses the
    same population to keep the two columns consistent.

    Listed smallest cake first, so the per-slice column reads as a price curve.
    The rows are still PICKED by frequency, though: the limit has to keep the most
    common sizes, or a single 4-slice one-off would push out the 12s and 16s the
    table exists to show. Hence the inner query orders by count and the outer one
    re-sorts that top-N by size.
    """
    rows = session.execute(
        text(
            f"SELECT p, c, avg_pp FROM ("  # nosec B608
            f"  SELECT o.portions AS p, COUNT(*) AS c, "
            f"         AVG(o.final_price / o.portions) AS avg_pp "
            f"  FROM offers o "
            f"  WHERE o.portions IS NOT NULL AND o.portions > 0 "
            f"    AND o.final_price IS NOT NULL "
            f"    AND {_year_guard(_LOCAL_CREATED)} "
            f"  GROUP BY o.portions ORDER BY c DESC, p ASC LIMIT :lim"
            f") t ORDER BY p ASC"
        ),
        {"year": year, "lim": limit},
    ).all()
    return [PortionStat(int(r.p), int(r.c), Decimal(r.avg_pp)) for r in rows]


def _avg_per_portion(session: Session, year: int | None) -> Decimal | None:
    """Overall average price per slice — the mean of each offer's own per-slice
    price (not total/total, which would let big cakes dominate the figure)."""
    value = _scalar(
        session,
        f"SELECT AVG(o.final_price / o.portions) FROM offers o "  # nosec B608
        f"WHERE o.portions IS NOT NULL AND o.portions > 0 "
        f"  AND o.final_price IS NOT NULL AND {_year_guard(_LOCAL_CREATED)}",
        year=year,
    )
    # AVG over NUMERIC comes back as Decimal; NULL (no qualifying rows) as None.
    return value if isinstance(value, Decimal) else None


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


def _done_split(session: Session, year: int | None) -> DoneSplit:
    """Break FINISHED (Kész) work into where the money went.

    The Alap group is listed per COMPONENT by name rather than hardcoding
    "Munkadíj"/"Rezsi" — the chef owns those component names, so this survives a
    rename and picks up a third service component automatically.
    """
    base_rows = [
        (str(r.k), Decimal(r.total))
        for r in session.execute(
            text(
                f"""
                SELECT c.name AS k, COALESCE(SUM(lc.line_price), 0) AS total
                FROM v_offer_line_cost lc
                JOIN offers o     ON o.id = lc.offer_id
                JOIN components c ON c.id = lc.component_id
                JOIN groups g     ON g.id = c.group_id
                WHERE g.name = :base_group AND o.status = 'done'
                  AND COALESCE(o.paid, o.final_price) IS NOT NULL
                  AND {_year_guard(_LOCAL_CREATED)}
                GROUP BY c.name ORDER BY total DESC, c.name
                """  # nosec B608
            ),
            {"year": year, "base_group": BASE_GROUP_NAME},
        ).all()
    ]
    materials = _money(
        session,
        f"""
        SELECT COALESCE(SUM(lc.line_price), 0)
        FROM v_offer_line_cost lc
        JOIN offers o     ON o.id = lc.offer_id
        JOIN components c ON c.id = lc.component_id
        JOIN groups g     ON g.id = c.group_id
        WHERE g.name <> :base_group AND o.status = 'done'
          AND COALESCE(o.paid, o.final_price) IS NOT NULL
          AND {_year_guard(_LOCAL_CREATED)}
        """,  # nosec B608
        year=year,
        base_group=BASE_GROUP_NAME,
    )
    # Tip = whatever was paid ABOVE the quoted price; never negative (a shortfall
    # is not a negative tip, it just means less was collected).
    tip = _money(
        session,
        f"""
        SELECT COALESCE(SUM(GREATEST(o.paid - o.final_price, 0)), 0)
        FROM offers o
        WHERE o.status = 'done' AND o.paid IS NOT NULL AND o.final_price IS NOT NULL
          AND {_year_guard(_LOCAL_CREATED)}
        """,  # nosec B608
        year=year,
    )
    # …and its mirror: quoted more than was ever collected. NOT a discount — an
    # intentional price cut shows up as (negative) Üzleti profit, because that is a
    # quote-vs-COST decision. This is quote-vs-CASH: money that should have arrived
    # and did not, so it should normally be zero. Non-negative like the tip, but
    # SUBTRACTED, or the block overshoots Bevétel.
    shortfall = _money(
        session,
        f"""
        SELECT COALESCE(SUM(GREATEST(o.final_price - o.paid, 0)), 0)
        FROM offers o
        WHERE o.status = 'done' AND o.paid IS NOT NULL AND o.final_price IS NOT NULL
          AND {_year_guard(_LOCAL_CREATED)}
        """,  # nosec B608
        year=year,
    )
    # Money kept from offers the customer cancelled. It carries NO cost line and no
    # profit line: the cost queries above are scoped to 'done', so a cancellation
    # contributes revenue and nothing else. That is deliberate — the cake was never
    # made — and it is why the kept deposit needs its own row here to keep the block
    # reconciling. Folding it into Üzleti profit would read as margin earned on
    # delivered work, which it is not.
    cancellation = _money(
        session,
        f"""
        SELECT COALESCE(SUM(COALESCE(o.paid, 0)), 0)
        FROM offers o
        WHERE o.status = 'cancelled'
          AND {_year_guard(_LOCAL_CREATED)}
        """,  # nosec B608
        year=year,
    )
    return DoneSplit(
        base_rows=base_rows,
        tip=tip,
        shortfall=shortfall,
        cancellation=cancellation,
        materials=materials,
    )


def _biz_profit(session: Session, year: int | None) -> BizProfit:
    """Üzleti profit over finished (Kész/done) offers — quote minus computed cost.

    `avg_pct` is the mean of each offer's markup (final_price / calculated_price −
    1), matching the % on the offer sheet; the division is guarded to offers with
    a positive cost. `paid` is deliberately not consulted (see BizProfit)."""
    row = session.execute(
        text(
            f"""
            SELECT
              COUNT(*) AS cnt,
              COALESCE(SUM(COALESCE(o.final_price, o.paid) - vc.calculated_price), 0) AS total,
              COALESCE(AVG(COALESCE(o.final_price, o.paid) - vc.calculated_price), 0) AS avg,
              COALESCE(
                AVG(COALESCE(o.final_price, o.paid) / vc.calculated_price - 1)
                  FILTER (WHERE vc.calculated_price > 0), 0
              ) AS avg_pct
            FROM offers o
            JOIN v_offer_cost vc ON vc.offer_id = o.id
            WHERE o.status = 'done' AND COALESCE(o.final_price, o.paid) IS NOT NULL
              AND {_year_guard(_LOCAL_CREATED)}
            """  # nosec B608
        ),
        {"year": year},
    ).one()
    return BizProfit(
        count=int(row.cnt),
        total=Decimal(row.total),
        avg=Decimal(row.avg),
        avg_pct=float(row.avg_pct),
    )


def collect(session: Session, year: int | None) -> Stats:
    series, kind = _series(session, year)
    return Stats(
        year=year,
        years=available_years(session),
        kpis=_kpis(session, year),
        series=series,
        series_kind=kind,
        status_counts=_status_counts(session, year),
        top_sponges=_top(session, "sponge", year),
        top_flavors=_top(session, "flavor", year),
        top_themes=_top(session, "theme", year),
        by_portions=_by_portions(session, year),
        avg_per_portion=_avg_per_portion(session, year),
        done_split=_done_split(session, year),
        biz_profit=_biz_profit(session, year),
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
        (
            f'<svg class="cp-chart" viewBox="0 0 {width} {height}" role="img" '
            f'preserveAspectRatio="xMidYMid meet">'
        )
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
