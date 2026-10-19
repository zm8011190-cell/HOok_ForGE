import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.database import SessionLocal
from app.models.event import Delivery, DeliveryStatus
from app.services.dispatcher import PROCESSING_LEASE_SECONDS, dispatch_webhook


logger = logging.getLogger(__name__)


async def process_pending_deliveries(batch_size: int = 10) -> int:
    processed = 0
    async with SessionLocal() as db:
        for _ in range(batch_size):
            now = datetime.now(timezone.utc)
            statement = (
                select(Delivery.id)
                .where(
                    or_(
                        and_(
                            Delivery.status.in_(
                                (DeliveryStatus.PENDING, DeliveryStatus.RETRYING)
                            ),
                            or_(
                                Delivery.next_retry_at.is_(None),
                                Delivery.next_retry_at <= now,
                            ),
                        ),
                        and_(
                            Delivery.status == DeliveryStatus.PROCESSING,
                            Delivery.last_attempted_at
                            <= now - timedelta(seconds=PROCESSING_LEASE_SECONDS),
                        ),
                    ),
                )
                .order_by(Delivery.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            delivery_id = await db.scalar(statement)
            if delivery_id is None:
                break

            await dispatch_webhook(db, delivery_id)
            processed += 1
    return processed


async def poll_pending_deliveries(interval: int = 10) -> None:
    while True:
        try:
            processed = await process_pending_deliveries()
            if processed == 0:
                logger.debug("No due deliveries found")
        except Exception:
            logger.exception("Error processing pending deliveries")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(poll_pending_deliveries())
