"""Custom Prometheus metric: last successful price-sync run.

The sync job runs in a separate CronJob pod, so the app can't observe it
directly — it reads the price_sync_state row from the DB at scrape time and
exposes it as a gauge. AlertManager fires when it goes stale (> 25h). See the
PrometheusRule in the ArgoCD repo.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from prometheus_client import REGISTRY
from prometheus_client.core import GaugeMetricFamily

from app.db import SessionLocal
from app.models import PriceSyncState

logger = logging.getLogger(__name__)

_METRIC = "cake_pricing_price_sync_last_success_timestamp_seconds"


class PriceSyncCollector:
    """Yields the last-success timestamp as a gauge, queried live per scrape."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        gauge = GaugeMetricFamily(
            _METRIC,
            "Unix time of the last successful daily price-sync run (absent if unknown).",
        )
        try:
            with SessionLocal() as session:
                state = session.get(PriceSyncState, 1)
            if state is not None and state.last_success_at is not None:
                gauge.add_metric([], state.last_success_at.timestamp())
        except Exception:  # noqa: BLE001 — a scrape must never crash on a DB blip
            # Emit NO sample (not 0). A 0 makes `time() - metric` look infinitely
            # stale, so a transient DB blip would page the staleness alert even
            # though the nightly job is fine. Absence is honest — we can't judge
            # freshness without the DB — and a sustained absence is caught by its
            # own `absent_over_time` alert (see prometheus-alarms in the ArgoCD repo).
            logger.exception("price-sync metric: DB read failed")
        yield gauge


def register() -> None:
    """Register the collector. Call exactly once at startup — REGISTRY.register
    raises on a duplicate collector, so this is not idempotent."""
    REGISTRY.register(PriceSyncCollector())
