from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.event import Delivery, Endpoint, Event


router = APIRouter(prefix="/events", tags=["events"])


class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    payload_json: dict
    idempotency_key: str = Field(min_length=1, max_length=255)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_event(
    event_create: EventCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    existing_event = await db.scalar(
        select(Event).where(Event.idempotency_key == event_create.idempotency_key)
    )
    if existing_event:
        if (
            existing_event.name != event_create.name
            or existing_event.payload_json != event_create.payload_json
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used for a different event",
            )
        response.status_code = status.HTTP_200_OK
        return {
            "id": existing_event.id,
            "name": existing_event.name,
            "payload_json": existing_event.payload_json,
        }

    new_event = Event(
        name=event_create.name,
        payload_json=event_create.payload_json,
        idempotency_key=event_create.idempotency_key,
    )
    db.add(new_event)
    endpoints = await db.scalars(select(Endpoint).where(Endpoint.is_active.is_(True)))
    for endpoint in endpoints:
        new_event.deliveries.append(Delivery(endpoint=endpoint, secret=endpoint.secret))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_event = await db.scalar(
            select(Event).where(
                Event.idempotency_key == event_create.idempotency_key
            )
        )
        if existing_event is None:
            raise
        if (
            existing_event.name != event_create.name
            or existing_event.payload_json != event_create.payload_json
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used for a different event",
            )
        response.status_code = status.HTTP_200_OK
        return {
            "id": existing_event.id,
            "name": existing_event.name,
            "payload_json": existing_event.payload_json,
        }
    await db.refresh(new_event)
    return {
        "id": new_event.id,
        "name": new_event.name,
        "payload_json": new_event.payload_json,
    }
