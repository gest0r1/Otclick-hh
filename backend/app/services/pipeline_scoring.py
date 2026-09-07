"""Hard filter + structured LLM scoring for persistent vacancy_pipeline rows.

The scorer is deliberately fail-closed: an LLM/config/parse failure moves the
vacancy to score_error. It never turns an unknown result into a positive match.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from pydantic import BaseModel, Field

from app.ai.agent import HHAgent
from app.config import settings
from app.db.supabase import service_client
from app.services import candidate_context_service, vacancy_pipeline, vacancy_review_service

logger = logging.getLogger(__name__)

MAX_SCORE_PER_RUN = 15


class ScoreComponents(BaseModel):
    role_fit: int = Field(ge=0, le=25)
    scale_fit: int = Field(ge=0, le=25)
    transformation_mandate: int = Field(ge=0, le=25)
    industry_business_context: int = Field(ge=0, le=25)


class StructuredVacancyScore(BaseModel):
    components: ScoreComponents
    pros: list[str] = Field(default_factory=list, max_length=8)
    risks: list[str] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=8)
    confidence: int = Field(ge=0, le=100)
    explanation: str = Field(min_length=1, max_length=4000)

    @property
    def total(self) -> int:
        c = self.components
        return c.role_fit + c.scale_fit + c.transformation_mandate + c.industry_business_context


_STRATEGIC_ACRONYM = re.compile(r"(?<![a-z0-9])(?:cio|cdto|cto)(?![a-z0-9])", re.IGNORECASE)
_STRATEGIC_TITLE_SIGNALS = (
    "ит директор",
    "it директор",
    "директор по ит",
    "директор по информационным технологиям",
    "директор по цифров",
    "цифровой трансформац",
    "digital transformation",
    "digital director",
    "technology director",
)

_HARD_TITLE_MISMATCHES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("системный администратор", "system administrator"), "role_mismatch_system_administration"),
    (("руководитель технической поддержки", "head of technical support"), "role_mismatch_support"),
    (("руководитель инфраструктуры", "директор по инфраструктуре", "head of infrastructure"), "role_mismatch_infrastructure"),
    (("программист 1с", "1c developer", "senior developer", "lead developer", "ведущий разработчик"), "role_mismatch_hands_on_development"),
)


def hard_filter_reason(vacancy: dict) -> str | None:
    """Only reject explicit title-level mismatches; ambiguity goes to the LLM.

    Candidate industry/scale preferences are not hard-filtered here because HH
    vacancy text often lacks reliable company scale/holding context. Unknown is
    intentionally not reject. Any explicit transformation/C-level signal wins
    over a technical word in the same title so mixed roles reach the scorer.
    """
    title = str(vacancy.get("title") or vacancy.get("name") or "").strip().lower()
    if not title:
        return None
    if _STRATEGIC_ACRONYM.search(title) or any(signal in title for signal in _STRATEGIC_TITLE_SIGNALS):
        return None
    for patterns, reason in _HARD_TITLE_MISMATCHES:
        if any(pattern in title for pattern in patterns):
            return reason
    return None


def _load_discovered(user_id: str, limit: int) -> list[dict]:
    res = (
        service_client.table("vacancy_pipeline")
        .select("id,hh_vacancy_id,title,employer_name,status")
        .eq("user_id", user_id)
        .eq("status", "discovered")
        .order("discovered_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def _context_for_prompt(context: dict) -> str:
    facts = [
        {
            "fact_key": fact.get("fact_key"),
            "category": fact.get("category"),
            "title": fact.get("title"),
            "statement": fact.get("statement"),
            "metrics": fact.get("metrics") or {},
            "tags": fact.get("tags") or [],
        }
        for fact in context.get("facts") or []
    ]
    return json.dumps(
        {
            "profile_version": context.get("version"),
            "profile": context.get("profile") or {},
            "confirmed_facts": facts,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _vacancy_for_prompt(vacancy: dict) -> str:
    return json.dumps(
        {
            "hh_vacancy_id": vacancy.get("hh_vacancy_id"),
            "title": vacancy.get("title"),
            "employer": vacancy.get("employer_name"),
            "area": vacancy.get("area_name"),
            "salary": vacancy.get("salary"),
            "description": vacancy.get("description"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _system_prompt() -> str:
    return """Ты оцениваешь соответствие вакансии карьерной стратегии кандидата CIO/CDTO.
Не продавай кандидата и не пиши сопроводительное письмо. Нужна строгая оценка fit.

Правила:
1. Используй факты о вакансии только из VACANCY. Не угадывай выручку, отрасль, размер компании, подчинение или компенсацию по названию бренда.
2. Используй факты о кандидате только из CANDIDATE. Не усиливай и не округляй метрики.
3. Unknown не равен mismatch. Если масштаб/отрасль/мандат не указаны, добавь это в unknowns и не ставь экстремально низкую оценку только из-за отсутствия данных.
4. Отличай CIO/CDTO трансформации от начальника эксплуатации/инфраструктуры. CTO высоко оценивай только при ответственности за платформу, архитектуру и продукты, а не hands-on development.
5. Для компаний >100 млрд подходящим может быть CIO-1/CDTO-1 при сильном трансформационном мандате.
6. Банки/bigtech/retail/e-commerce/дистрибуция как самостоятельное ядро — негативный сигнал; внутри диверсифицированного холдинга это не автоматический reject.
7. Компоненты по 0..25: role_fit, scale_fit, transformation_mandate, industry_business_context. Итог будет рассчитан приложением как их сумма.
8. confidence 0..100 отражает полноту данных, а не привлекательность вакансии.
9. pros/risks/unknowns — короткие конкретные пункты, без общих фраз.
"""


async def _score_with_llm(llm, context: dict, vacancy: dict) -> StructuredVacancyScore:
    if llm is None:
        raise RuntimeError("llm_not_configured")
    model = llm.with_structured_output(StructuredVacancyScore)
    result = await model.ainvoke(
        [
            ("system", _system_prompt()),
            (
                "human",
                "CANDIDATE:\n"
                + _context_for_prompt(context)
                + "\n\nVACANCY:\n"
                + _vacancy_for_prompt(vacancy),
            ),
        ]
    )
    return StructuredVacancyScore.model_validate(result) if isinstance(result, dict) else result


async def _mark_error(user_id: str, pipeline_id: str, error: Exception | str) -> None:
    message = str(error)[:2000] or "unknown_scoring_error"
    await asyncio.to_thread(
        vacancy_pipeline.transition,
        user_id=user_id,
        pipeline_id=pipeline_id,
        from_statuses=["scoring"],
        to_status="score_error",
        changes={
            "score": None,
            "score_details": {"error": message},
            "score_explanation": message,
        },
    )


async def score_one(user_id: str, row: dict, *, context: dict, llm) -> str:
    pipeline_id = str(row["id"])
    claimed = await asyncio.to_thread(
        vacancy_pipeline.transition,
        user_id=user_id,
        pipeline_id=pipeline_id,
        from_statuses=["discovered"],
        to_status="scoring",
    )
    if not claimed:
        return "skipped"

    try:
        vacancy, enrichment_state = await vacancy_review_service.enrich(user_id, pipeline_id)
        if enrichment_state["archived"]:
            return "archived"

        reason = hard_filter_reason(vacancy)
        if reason:
            await asyncio.to_thread(
                vacancy_pipeline.transition,
                user_id=user_id,
                pipeline_id=pipeline_id,
                from_statuses=["scoring"],
                to_status="scored",
                changes={
                    "score": 0,
                    "hard_filter_reason": reason,
                    "score_details": {
                        "hard_filter": True,
                        "profile_version": context.get("version"),
                    },
                    "score_explanation": f"Hard filter: {reason}",
                },
            )
            return "hard_filtered"

        result = await _score_with_llm(llm, context, vacancy)
        details = {
            "components": result.components.model_dump(),
            "pros": result.pros,
            "risks": result.risks,
            "unknowns": result.unknowns,
            "confidence": result.confidence,
            "profile_version": context.get("version"),
            "model": settings.OPENAI_MODEL,
        }
        changed = await asyncio.to_thread(
            vacancy_pipeline.transition,
            user_id=user_id,
            pipeline_id=pipeline_id,
            from_statuses=["scoring"],
            to_status="scored",
            changes={
                "score": result.total,
                "score_details": details,
                "score_explanation": result.explanation,
                "hard_filter_reason": None,
            },
        )
        return "scored" if changed else "skipped"
    except Exception as ex:
        logger.warning("scoring failed user=%s vacancy=%s", user_id, pipeline_id, exc_info=True)
        await _mark_error(user_id, pipeline_id, ex)
        return "error"


async def score_user(user_id: str, limit: int = MAX_SCORE_PER_RUN) -> dict[str, int]:
    rows = await asyncio.to_thread(_load_discovered, user_id, limit)
    summary = {
        "found": len(rows),
        "scored": 0,
        "hard_filtered": 0,
        "archived": 0,
        "errors": 0,
        "skipped": 0,
    }
    if not rows:
        return summary

    try:
        context = await candidate_context_service.load_candidate_context(user_id)
    except Exception as ex:
        logger.warning("candidate context unavailable user=%s", user_id, exc_info=True)
        for row in rows:
            pipeline_id = str(row["id"])
            claimed = await asyncio.to_thread(
                vacancy_pipeline.transition,
                user_id=user_id,
                pipeline_id=pipeline_id,
                from_statuses=["discovered"],
                to_status="scoring",
            )
            if claimed:
                await _mark_error(user_id, pipeline_id, ex)
                summary["errors"] += 1
            else:
                summary["skipped"] += 1
        return summary

    llm = HHAgent(user_id).llm
    for row in rows:
        outcome = await score_one(user_id, row, context=context, llm=llm)
        if outcome == "scored":
            summary["scored"] += 1
        elif outcome == "hard_filtered":
            summary["hard_filtered"] += 1
        elif outcome == "archived":
            summary["archived"] += 1
        elif outcome == "error":
            summary["errors"] += 1
        else:
            summary["skipped"] += 1
    logger.info("scoring user=%s summary=%s", user_id, summary)
    return summary
