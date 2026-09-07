from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SendQueueItem(BaseModel):
    id: str
    vacancy_pipeline_id: str
    resume_id: str | None = None
    hh_vacancy_id: str
    approved_letter_hash: str
    status: Literal["queued", "sending", "sent", "failed", "manual_required", "cancelled"]
    attempts: int
    last_error: str | None = None
    queued_at: str
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str
