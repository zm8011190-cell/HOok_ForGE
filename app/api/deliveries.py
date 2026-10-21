from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.event import Delivery, DeliveryStatus


router = APIRouter(prefix="/deliveries", tags=["deliveries"])


@router.get("", status_code=status.HTTP_200_OK)
async def list_deliveries(db: AsyncSession = Depends(get_db)):
    deliveries = await db.scalars(select(Delivery).order_by(Delivery.id.desc()).limit(50))
    return [
        {
            "id": delivery.id,
            "event_id": delivery.event_id,
            "endpoint_id": delivery.endpoint_id,
            "status": delivery.status.value,
            "delivery_attempts": delivery.delivery_attempts,
            "last_attempted_at": delivery.last_attempted_at,
            "latency_ms": delivery.latency_ms,
            "response_status_code": delivery.response_status_code,
            "response_body": delivery.response_body,
            "error_message": delivery.error_message,
        }
        for delivery in deliveries
    ]


@router.post("/{delivery_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_delivery(delivery_id: int, db: AsyncSession = Depends(get_db)):
    delivery = await db.get(Delivery, delivery_id)
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found"
        )
    if delivery.status not in (DeliveryStatus.FAILED, DeliveryStatus.RETRYING):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only failed or retrying deliveries can be retried",
        )

    delivery.status = DeliveryStatus.RETRYING
    delivery.delivery_attempts = 0
    delivery.next_retry_at = datetime.now(timezone.utc)
    delivery.last_attempted_at = None
    delivery.latency_ms = None
    delivery.response_status_code = None
    delivery.response_body = None
    delivery.error_message = None
    await db.commit()
    return {"message": f"Delivery {delivery_id} queued for retry"}
