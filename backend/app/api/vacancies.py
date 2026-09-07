from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.schemas.vacancies import (
    CoverLetterDraftUpdate,
    VacancyDecisionRequest,
    VacancyEnrichmentResponse,
    VacancyPipelineResponse,
)
from app.services import pipeline_cover_letters, send_queue_service, vacancy_review_service


router = APIRouter(prefix="/api/vacancies", tags=["vacancies"])


@router.get("", response_model=list[VacancyPipelineResponse])
async def list_all(
    status: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
):
    rows = await vacancy_review_service.list_vacancies(
        user_id,
        statuses=status,
        limit=limit,
        offset=offset,
    )
    return [VacancyPipelineResponse(**row) for row in rows]


@router.get("/{pipeline_id}", response_model=VacancyPipelineResponse)
async def get_one(
    pipeline_id: str,
    user_id: str = Depends(get_current_user),
):
    row = await vacancy_review_service.get_vacancy(user_id, pipeline_id)
    return VacancyPipelineResponse(**row)


@router.post("/{pipeline_id}/decision", response_model=VacancyPipelineResponse)
async def decision(
    pipeline_id: str,
    body: VacancyDecisionRequest,
    user_id: str = Depends(get_current_user),
):
    row = await vacancy_review_service.decide(
        user_id,
        pipeline_id,
        action=body.action,
        reason=body.reason,
    )
    return VacancyPipelineResponse(**row)


@router.post("/{pipeline_id}/enrich", response_model=VacancyEnrichmentResponse)
async def enrich(
    pipeline_id: str,
    user_id: str = Depends(get_current_user),
):
    row, state = await vacancy_review_service.enrich(user_id, pipeline_id)
    return VacancyEnrichmentResponse(
        vacancy=VacancyPipelineResponse(**row),
        already_responded=state["already_responded"],
        archived=state["archived"],
    )


@router.post("/{pipeline_id}/cover-letter/generate", response_model=VacancyPipelineResponse)
async def generate_cover_letter(
    pipeline_id: str,
    user_id: str = Depends(get_current_user),
):
    row = await pipeline_cover_letters.generate_draft(user_id, pipeline_id)
    return VacancyPipelineResponse(**row)


@router.put("/{pipeline_id}/cover-letter", response_model=VacancyPipelineResponse)
async def update_cover_letter(
    pipeline_id: str,
    body: CoverLetterDraftUpdate,
    user_id: str = Depends(get_current_user),
):
    row = await pipeline_cover_letters.save_draft(user_id, pipeline_id, body.text)
    return VacancyPipelineResponse(**row)


@router.post("/{pipeline_id}/cover-letter/approve", response_model=VacancyPipelineResponse)
async def approve_cover_letter(
    pipeline_id: str,
    user_id: str = Depends(get_current_user),
):
    row = await send_queue_service.approve_letter(user_id, pipeline_id)
    return VacancyPipelineResponse(**row)
