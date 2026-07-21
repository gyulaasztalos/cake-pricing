"""Daily price-sync job (§ automatic price update). CronJob entrypoint:

    python -m app.jobs.price_sync

Steps, each of which raises on failure so the CronJob (and thus AlertManager)
sees the failure:
  1. download the árfigyelő XLSX,
  2. parse product_id -> average Maximum ár,
  3. reconcile every component that has a Termék azonosító (temporal price
     changes; mark not-found ones),
  4. commit,
  5. if anything changed OR any id was not found, e-mail a report to the shop,
  6. record last-success (for the /metrics staleness gauge).

A failure in 1–4 aborts before the commit (nothing half-applied). The report
mail (5) is best-effort by design only in that a mail failure still raises and
fails the job — but the DB changes are already committed, so a retry will find
"no changes" and simply not re-send. last-success (6) is written only after the
mail succeeds, so a mail outage keeps the staleness alarm honest.
"""

from __future__ import annotations

import logging
import sys

from app.config import settings
from app.db import SessionLocal
from app.services import mailer, price_sync

logger = logging.getLogger("price_sync")


def run() -> int:
    """Run one sync. Returns the number of price changes. Raises on any failure."""
    logger.info("price sync: downloading %s", settings.price_sync_url)
    data = price_sync.fetch_xlsx(settings.price_sync_url)
    prices = price_sync.parse_prices(data)
    logger.info("price sync: parsed %d product prices", len(prices))

    session = SessionLocal()
    try:
        result = price_sync.run_sync(session, prices)
        session.commit()  # durable BEFORE we e-mail / mark success
        logger.info(
            "price sync: checked=%d changed=%d missing=%d",
            result.checked,
            len(result.changes),
            len(result.missing),
        )

        # A report goes out when there are price changes OR unresolved ids.
        if result.changes or result.missing:
            mailer.send_price_report(result)
            logger.info("price sync: report e-mail sent")

        price_sync.mark_success(session)
        session.commit()
        return len(result.changes)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        changed = run()
    except Exception:
        logger.exception("price sync FAILED")
        sys.exit(1)
    logger.info("price sync OK (%d changes)", changed)


if __name__ == "__main__":
    main()
