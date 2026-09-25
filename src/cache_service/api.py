"""HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from cache_service.database import get_session
from cache_service.payloads import get_or_create_payload, get_payload
from cache_service.schemas import (
    HealthResponse,
    PayloadCreateRequest,
    PayloadCreateResponse,
    PayloadReadResponse,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post(
    "/payload",
    response_model=PayloadCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"description": "An identical payload already existed"}},
    tags=["payload"],
)
async def create_payload(
    request: PayloadCreateRequest, session: SessionDep, response: Response
) -> PayloadCreateResponse:
    payload, created = await get_or_create_payload(session, request.list_1, request.list_2)
    if created:
        return PayloadCreateResponse(id=payload.id, message="Payload created", reused=False)

    # Nothing was created: the inputs map onto an existing payload identifier.
    response.status_code = status.HTTP_200_OK
    return PayloadCreateResponse(
        id=payload.id, message="Payload already exists, reusing identifier", reused=True
    )


@router.get(
    "/payload/{payload_id}",
    response_model=PayloadReadResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown payload identifier"}},
    tags=["payload"],
)
async def read_payload(payload_id: str, session: SessionDep) -> PayloadReadResponse:
    payload = await get_payload(session, payload_id)
    if payload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payload not found")
    return PayloadReadResponse(output=payload.output)
