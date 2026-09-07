from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class VacancySourceRef(BaseModel):
    id: str
    name: str
    source_type: str


class VacancyPipelineResponse(BaseModel):
    id: str
    resume_id: str | None = None
    hh_vacancy_id: str
    vacancy_url: str | None = None
    title: str
    employer_id: str | None = None
    employer_name: str | None = None
    area_name: str | None = None
    salary: dict[str, Any] | None = None
    published_at: str | None = None
    discovered_at: str
    last_seen_at: str
    description: str | None = None
    status: str
    score: int | None = None
    score_details: dict[str, Any] | None = None
    score_explanation: str | None = None
    hard_filter_reason: str | None = None
    user_decision_reason: str | None = None
    cover_letter_draft: str | None = None
    approved_at: str | None = None
    created_at: str
    updated_at: str
    sources: list[VacancySourceRef] = Field(default_factory=list)


class VacancyDecisionRequest(BaseModel):
    action: Literal["select", "reject", "hold", "review"]
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def normalise_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class VacancyEnrichmentResponse(BaseModel):
    vacancy: VacancyPipelineResponse
    already_responded: bool = False
    archived: bool = False
