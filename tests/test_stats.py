"""Statistics service: KPIs, series scoping, breakdowns + the SVG chart helper.

The DB-backed tests need a real Postgres (v_offer_cost is a SQL view); they skip
without DATABASE_URL. The chart-geometry tests are pure and always run.
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest

from app.services import stats as stats_svc

BKK = dt.timezone(dt.timedelta(hours=2))  # Budapest summer offset; year/month only


def _dt(year: int, month: int = 6, day: int = 15) -> dt.datetime:
    return dt.datetime(year, month, day, 12, 0, tzinfo=BKK)


# --- pure chart helper (no DB) ----------------------------------------------


def test_bar_chart_empty_renders_placeholder():
    out = str(stats_svc.bar_chart([], []))
    assert "<svg" not in out
    assert "—" in out


def test_bar_chart_renders_one_rect_per_value():
    out = str(stats_svc.bar_chart(["1", "2", "3"], [10, 20, 0]))
    # one primary bar rect per value (the zero bar still draws, h=0)
    assert out.count('<rect class="cp-bar"') == 3
    assert "<svg" in out and out.rstrip().endswith("</svg>")


def test_bar_chart_highlight_adds_subbars():
    out = str(stats_svc.bar_chart(["1", "2"], [10, 20], highlights=[5, 10]))
    assert out.count("cp-bar--hi") == 2


def test_bar_chart_money_uses_compact_labels():
    out = str(stats_svc.bar_chart(["1"], [1_250_000], money=True))
    assert "1,2M" in out  # 1.25M → one-decimal compact


def test_bar_chart_escapes_labels():
    # Labels are numeric in practice, but the helper must still escape them.
    out = str(stats_svc.bar_chart(["<x>"], [1]))
    assert "<x>" not in out
    assert "&lt;x&gt;" in out


# --- DB-backed aggregation ---------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytest.fixture
def session():
    from app.db import SessionLocal

    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


def _make_offer(session, customer_id, comp_id, *, status, when, final, flavor=None, theme=None):
    from app.models import Offer, OfferComponent

    o = Offer(
        customer_id=customer_id,
        status=status,
        entry_date=when,
        final_price=Decimal(final) if final is not None else None,
        flavor=flavor,
        theme=theme,
    )
    session.add(o)
    session.flush()
    session.add(OfferComponent(offer_id=o.id, component_id=comp_id, amount=Decimal("1000")))
    session.commit()
    return o.id


@pytestmark_db
def test_collect_kpis_and_scoping(clean_db, seed_component):
    from app.db import SessionLocal
    from app.models import Customer

    # A component priced 500 Ft per 1000 unit → a 1000-unit line costs 500 Ft.
    comp = seed_component("Piskóta", "Piskóta", "g", "ingredient", "1000", "500")

    s = SessionLocal()
    cust = Customer(name="Teszt Ügyfél", entry_date=_dt(2025, 3, 1))
    s.add(cust)
    s.commit()
    cid = cust.id

    # 2025: 2 accepted + 1 done (won=3), 1 rejected, 1 sent, 1 draft.
    for st in ("accepted", "accepted", "done"):
        _make_offer(s, cid, comp, status=st, when=_dt(2025), final="10000", flavor="Csoki")
    _make_offer(s, cid, comp, status="rejected", when=_dt(2025), final="9000", flavor="Vanília")
    _make_offer(s, cid, comp, status="sent", when=_dt(2025), final="9000", theme="Unikornis")
    _make_offer(s, cid, comp, status="draft", when=_dt(2025), final=None)
    # 2024: one won offer, to exercise year scoping + the yearly series.
    _make_offer(s, cid, comp, status="done", when=_dt(2024), final="5000")
    s.close()

    all_time = stats_svc.collect(SessionLocal(), None)
    assert all_time.kpis.total == 7
    assert all_time.kpis.won == 4  # 3 in 2025 + 1 in 2024
    assert all_time.series_kind == "year"
    assert {p.label for p in all_time.series} == {"2024", "2025"}

    y2025 = stats_svc.collect(SessionLocal(), 2025)
    k = y2025.kpis
    assert k.total == 6
    assert k.won == 3
    assert k.sent_out == 5  # sent + accepted*2 + rejected + done
    assert k.win_rate == pytest.approx(3 / 5)
    assert k.revenue == Decimal("30000")  # 3 won × 10000
    assert k.cost == Decimal("1500")  # 3 won × 500
    assert k.margin == Decimal("28500")
    assert k.drafts == 1
    assert k.new_customers == 1
    # Monthly series is a dense 12-point axis; all offers are in June (month 6).
    assert y2025.series_kind == "month"
    assert len(y2025.series) == 12
    assert y2025.series[5].offers == 6  # index 5 == June

    # Breakdowns for 2025.
    status_map = dict(y2025.status_counts)
    assert status_map["accepted"] == 2 and status_map["draft"] == 1
    assert ("Csoki", 3) in y2025.top_flavors
    assert y2025.source_split["internal"] == 6
