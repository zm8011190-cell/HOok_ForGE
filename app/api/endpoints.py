import secrets

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.event import Endpoint
from app.services.security import validate_webhook_url


router = APIRouter(prefix="/endpoints", tags=["endpoints"])


class EndpointCreateRequest(BaseModel):
    url: HttpUrl


class EndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    is_active: bool


class EndpointRegistrationResponse(EndpointResponse):
    secret: str


@router.post(
    "",
    response_model=EndpointRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_endpoint(
    body: EndpointCreateRequest, db: AsyncSession = Depends(get_db)
):
    url = str(body.url)
    validate_webhook_url(url)
    new_endpoint = Endpoint(
        url=url,
        secret=f"whsec_{secrets.token_hex(32)}",
        is_active=True,
    )
    db.add(new_endpoint)
    await db.commit()
    await db.refresh(new_endpoint)
    return new_endpoint


@router.get("", response_model=list[EndpointResponse], status_code=status.HTTP_200_OK)
async def get_endpoints(db: AsyncSession = Depends(get_db)):
    endpoints = await db.scalars(select(Endpoint).order_by(Endpoint.id))
    return endpoints.all()
