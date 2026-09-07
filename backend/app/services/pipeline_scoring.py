"""Hard filter + structured LLM scoring for persistent vacancy_pipeline rows.

The scorer is deliberately fail-closed: an LLM/config/parse failure moves the
vacancy to score_error. It never turns an unknown result into a positive match.
Only ACTIVE user-approved rules participate; proposals are invisible here.
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
from app.services import (
    candidate_context_service,
    selection_rules,
    vacancy_pipeline,
    vacancy_review_service,
)

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


def _matching_rules(vacancy: dict, rules: list[dict] | None) -> list[dict]:
    return [
        rule
        for rule in (rules or [])
        if rule.get("active", True)
        and isinstance(rule.get("match"), dict)
        and selection_rules.vacancy_matches(vacancy, rule["match"])
    ]


def hard_filter_reason(vacancy: dict, rules: list[dict] | None = None) -> str | None:
    """Reject explicit built-in mismatches and approved hard-reject rules only.

    Approved user rules are evaluated before the built-in strategic-title escape:
    if the user explicitly approved an absolute exclusion (for example, a company
    or business type), a CIO title must not silently override it.
    """
    for rule in _matching_rules(vacancy, rules):
        if rule.get("action") == "hard_reject":
            return f"approved_rule:{rule.get('id')}:{rule.get('name') or 'hard_reject'}"

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


def _rules_for_prompt(rules: list[dict]) -> str:
    return json.dumps(
        [
            {
                "id": rule.get("id"),
                "version": rule.get("version"),
                "name": rule.get("name"),
                "instruction": rule.get("instruction"),
            }
            for rule in rules
            if rule.get("action") == "scoring_preference"
        ],
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
3. APPROVED_SCORING_RULES — только явно одобренные пользователем предпочтения, match которых уже сработал на этой вакансии. Учитывай их в оценке; не превращай scoring preference в автоматический reject.
4. Unknown не равен mismatch. Если масштаб/отрасль/мандат не указаны, добавь это в unknowns и не ставь экстремально низкую оценку только из-за отсутствия данных.
5. Отличай CIO/CDTO трансформации от начальника эксплуатации/инфраструктуры. CTO высоко оценивай только при ответственности за платформу, архитектуру и продукты, а не hands-on development.
6. Для компаний >100 млрд подходящим может быть CIO-1/CDTO-1 при сильном трансформационном мандате.
7. Банки/bigtech/retail/e-commerce/дистрибуция как самостоятельное ядро — негативный сигнал; внутри диверсифицированного холдинга это не автоматический reject.
8. Компоненты по 0..25: role_fit, scale_fit, transformation_mandate, industry_business_context. Итог будет рассчитан приложением как их сумма.
9. confidence 0..100 отражает полноту данных, а не привлекательность вакансии.
10. pros/risks/unknowns — короткие конкретные пункты, без общих фраз.
"""


async def _score_with_llm(
    llm,
    context: dict,
    vacancy: dict,
    matched_rules: list[dict] | None = None,
) -> StructuredVacancyScore:
    if llm is None:
        raise RuntimeError("llm_not_configured")
    model = llm.with_structured_output(StructuredVacancyScore)
    scoring_rules = [
        rule for rule in (matched_rules or []) if rule.get("action") == "scoring_preference"
    ]
    result = await model.ainvoke(
        [
            ("system", _system_prompt()),
            (
                "human",
                "CANDIDATE:\n"
                + _context_for_prompt(context)
                + "\n\nAPPROVED_SCORING_RULES:\n"
                + _rules_for_prompt(scoring_rules)
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


async def score_one(
    user_id: str,
    row: dict,
    *,
    context: dict,
    llm,
    rules: list[dict] | None = None,
) -> str:
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

        matched_rules = _matching_rules(vacancy, rules)
        reason = hard_filter_reason(vacancy, matched_rules)
        applied_versions = [
            int(rule["version"])
            for rule in matched_rules
            if rule.get("version") is not None
        ]
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
                        "applied_rule_versions": applied_versions,
                    },
                    "score_explanation": f"Hard filter: {reason}",
                },
            )
            return "hard_filtered"

        result = await _score_with_llm(llm, context, vacancy, matched_rules)
        details = {
            "components": result.components.model_dump(),
            "pros": result.pros,
            "risks": result.risks,
            "unknowns": result.unknowns,
            "confidence": result.confidence,
            "profile_version": context.get("version"),
            "model": settings.OPENAI_MODEL,
            "applied_rule_versions": applied_versions,
            "applied_rule_ids": [str(rule.get("id")) for rule in matched_rules if rule.get("id")],
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
        context, rules = await asyncio.gather(
            candidate_context_service.load_candidate_context(user_id),
            selection_rules.load_active_rules(user_id),
        )
    except Exception as ex:
        logger.warning("candidate context/rules unavailable user=%s", user_id, exc_info=True)
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
        outcome = await score_one(user_id, row, context=context, llm=llm, rules=rules)
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
