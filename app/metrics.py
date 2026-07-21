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
            "Unix time of the last successful daily price-sync run (0 if unknown).",
        )
        value = 0.0
        try:
            with SessionLocal() as session:
                state = session.get(PriceSyncState, 1)
                if state is not None and state.last_success_at is not None:
                    value = state.last_success_at.timestamp()
        except Exception:  # noqa: BLE001 — a scrape must never crash on a DB blip
            logger.exception("price-sync metric: DB read failed")
        gauge.add_metric([], value)
        yield gauge


def register() -> None:
    """Register the collector once (idempotent per process)."""
    REGISTRY.register(PriceSyncCollector())
