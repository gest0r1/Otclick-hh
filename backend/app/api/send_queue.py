from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.schemas.send_queue import SendQueueItem
from app.services import send_queue_service


router = APIRouter(prefix="/api/send-queue", tags=["send-queue"])


@router.get("", response_model=list[SendQueueItem])
async def list_all(
    status: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    user_id: str = Depends(get_current_user),
):
    rows = await send_queue_service.list_queue(user_id, statuses=status, limit=limit)
    return [SendQueueItem(**row) for row in rows]


@router.post("/{pipeline_id}", response_model=SendQueueItem)
async def queue_one(
    pipeline_id: str,
    user_id: str = Depends(get_current_user),
):
    row = await send_queue_service.queue_approved(user_id, pipeline_id)
    return SendQueueItem(**row)


@router.delete("/{pipeline_id}", response_model=SendQueueItem)
async def cancel_one(
    pipeline_id: str,
    user_id: str = Depends(get_current_user),
):
    row = await send_queue_service.cancel_queued(user_id, pipeline_id)
    return SendQueueItem(**row)
