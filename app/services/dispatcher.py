import hashlib
import hmac
import json
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.event import Delivery, DeliveryStatus


logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
REQUEST_TIMEOUT_SECONDS = 10.2
PROCESSING_LEASE_SECONDS = 60


async def dispatch_webhook(db: AsyncSession, delivery_id: int) -> None:
    result = await db.execute(
        select(Delivery)
        .options(selectinload(Delivery.event), selectinload(Delivery.endpoint))
        .where(Delivery.id == delivery_id)
    )
    delivery = result.scalar_one_or_none()
    if delivery is None or delivery.status not in (
        DeliveryStatus.PENDING,
        DeliveryStatus.RETRYING,
        DeliveryStatus.PROCESSING,
    ):
        logger.warning("Delivery %s not found or not eligible for dispatch", delivery_id)
        return
    if not delivery.endpoint.is_active:
        delivery.status = DeliveryStatus.FAILED
        delivery.error_message = "Endpoint is inactive"
        await db.commit()
        return

    now = datetime.now(timezone.utc)
    delivery.delivery_attempts += 1
    delivery.last_attempted_at = now
    delivery.status = DeliveryStatus.PROCESSING
    delivery.next_retry_at = now + timedelta(seconds=PROCESSING_LEASE_SECONDS)
    await db.commit()

    payload_body = json.dumps(
        delivery.event.payload_json, separators=(",", ":"), ensure_ascii=False
    )
    signature = generate_signature(payload_body, delivery.secret)
    headers = {
        "Content-Type": "application/json",
        "X-Hook-Signature": signature,
        "X-Hook-Delivery-Id": str(delivery.id),
        "X-Hook-Event": delivery.event.name,
        "User-Agent": "Webhook-Dispatcher/1.0",
    }
    start_time = datetime.now(timezone.utc)
    status_code = None
    response_body = None
    error_message = None

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.post(
                delivery.endpoint.url, content=payload_body, headers=headers
            )
        status_code = response.status_code
        response_body = response.text[:1000]
    except httpx.HTTPError as exc:
        error_message = f"Request failed: {exc}"
        logger.warning("Delivery %s request failed: %s", delivery_id, exc)

    delivery.latency_ms = int(
        (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
    )
    delivery.response_status_code = status_code
    delivery.response_body = response_body
    delivery.error_message = error_message

    if status_code is not None and 200 <= status_code < 300:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.next_retry_at = None
        logger.info("Delivery %s succeeded with status %s", delivery_id, status_code)
    elif (
        delivery.delivery_attempts >= MAX_ATTEMPTS
        or (
            status_code is not None
            and 400 <= status_code < 500
            and status_code not in (408, 429)
        )
    ):
        delivery.status = DeliveryStatus.FAILED
        delivery.next_retry_at = None
        if error_message is None:
            delivery.error_message = f"Endpoint returned HTTP {status_code}"
        logger.warning(
            "Delivery %s failed after %s attempts",
            delivery_id,
            delivery.delivery_attempts,
        )
    else:
        delivery.status = DeliveryStatus.RETRYING
        delivery.next_retry_at = calculate_backoff_time(delivery.delivery_attempts)
        if error_message is None:
            delivery.error_message = f"Endpoint returned HTTP {status_code}"
        logger.info(
            "Delivery %s scheduled for retry at %s",
            delivery_id,
            delivery.next_retry_at,
        )

    await db.commit()


def generate_signature(payload: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def calculate_backoff_time(attempts: int) -> datetime:
    delay_seconds = 2**attempts + random.uniform(0, 1)
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
