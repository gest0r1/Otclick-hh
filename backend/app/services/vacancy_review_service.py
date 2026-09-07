"""Read/review operations for the persistent vacancy funnel.

This module is intentionally separate from the legacy applications history and
from the send path. User review only changes vacancy_pipeline lifecycle state;
it never calls HH submit endpoints.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import HTTPException, status

from app.db.supabase import service_client
from app.hh import vacancy_page, web
from app.services import vacancy_pipeline


_VACANCY_COLUMNS = (
    "id,resume_id,hh_vacancy_id,vacancy_url,title,employer_id,employer_name,"
    "area_name,salary,published_at,discovered_at,last_seen_at,description,status,"
    "score,score_details,score_explanation,hard_filter_reason,user_decision_reason,"
    "cover_letter_draft,cover_letter_meta,approved_at,created_at,updated_at"
)

_DECISION_TARGET = {
    "select": "selected",
    "reject": "rejected_by_user",
    "hold": "hold",
    "review": "review",
}

# Terminal/in-flight states must not be silently moved by a review click.
_REVIEWABLE_STATUSES = frozenset(
    {
        "discovered",
        "scored",
        "review",
        "selected",
        "letter_draft",
        "rejected_by_user",
        "hold",
        "score_error",
    }
)


def _get_owned(user_id: str, pipeline_id: str) -> dict:
    res = (
        service_client.table("vacancy_pipeline")
        .select(_VACANCY_COLUMNS)
        .eq("user_id", user_id)
        .eq("id", pipeline_id)
        .maybe_single()
        .execute()
    )
    if not (res and res.data):
        raise HTTPException(status_code=404, detail="vacancy not found")
    return res.data


def _source_map(user_id: str, vacancy_ids: list[str]) -> dict[str, list[dict]]:
    if not vacancy_ids:
        return {}

    links_res = (
        service_client.table("vacancy_pipeline_sources")
        .select("vacancy_id,source_id")
        .in_("vacancy_id", vacancy_ids)
        .execute()
    )
    links = links_res.data or []
    source_ids = list(
        dict.fromkeys(str(row["source_id"]) for row in links if row.get("source_id"))
    )
    if not source_ids:
        return {}

    sources_res = (
        service_client.table("vacancy_search_sources")
        .select("id,name,source_type")
        .eq("user_id", user_id)
        .in_("id", source_ids)
        .execute()
    )
    by_id = {str(row["id"]): row for row in (sources_res.data or [])}
    mapped: dict[str, list[dict]] = defaultdict(list)
    for link in links:
        source = by_id.get(str(link.get("source_id") or ""))
        if source:
            mapped[str(link["vacancy_id"])].append(source)
    return dict(mapped)


def _attach_sources(user_id: str, rows: list[dict]) -> list[dict]:
    sources = _source_map(user_id, [str(row["id"]) for row in rows])
    return [{**row, "sources": sources.get(str(row["id"]), [])} for row in rows]


async def list_vacancies(
    user_id: str,
    *,
    statuses: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    unknown = [s for s in (statuses or []) if s not in vacancy_pipeline.PIPELINE_STATUSES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown vacancy status(es): {', '.join(unknown)}",
        )

    def _query():
        q = (
            service_client.table("vacancy_pipeline")
            .select(_VACANCY_COLUMNS)
            .eq("user_id", user_id)
        )
        if statuses:
            q = q.in_("status", list(dict.fromkeys(statuses)))
        return q.order("discovered_at", desc=True).range(offset, offset + limit - 1).execute()

    res = await asyncio.to_thread(_query)
    rows = res.data or []
    return await asyncio.to_thread(_attach_sources, user_id, rows)


async def get_vacancy(user_id: str, pipeline_id: str) -> dict:
    row = await asyncio.to_thread(_get_owned, user_id, pipeline_id)
    return (await asyncio.to_thread(_attach_sources, user_id, [row]))[0]


async def decide(
    user_id: str,
    pipeline_id: str,
    *,
    action: str,
    reason: str | None,
) -> dict:
    if action not in _DECISION_TARGET:
        raise HTTPException(status_code=400, detail="unknown vacancy decision")
    if action == "reject" and not reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="rejection reason is required",
        )

    current = await asyncio.to_thread(_get_owned, user_id, pipeline_id)
    current_status = current["status"]
    if current_status not in _REVIEWABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"vacancy cannot be reviewed from status {current_status}",
        )

    target = _DECISION_TARGET[action]
    changes = {
        "user_decision_reason": reason if action in {"reject", "hold"} else None,
    }
    changed = await asyncio.to_thread(
        vacancy_pipeline.transition,
        user_id=user_id,
        pipeline_id=pipeline_id,
        from_statuses=[current_status],
        to_status=target,
        changes=changes,
    )
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="vacancy changed concurrently; reload and retry",
        )
    return await get_vacancy(user_id, pipeline_id)


async def enrich(user_id: str, pipeline_id: str) -> tuple[dict, dict]:
    """Fetch the current HH vacancy page and persist the full text snapshot.

    This is read-only against HH. A removed/closed vacancy is moved to archived;
    a missing description is treated as a contract failure and is never allowed
    to look like a successfully enriched vacancy for the scorer.
    """
    current = await asyncio.to_thread(_get_owned, user_id, pipeline_id)
    try:
        full = await vacancy_page.get_full_vacancy(user_id, current["hh_vacancy_id"])
    except web.VacancyGone:
        if current["status"] != "sent":
            await asyncio.to_thread(
                vacancy_pipeline.transition,
                user_id=user_id,
                pipeline_id=pipeline_id,
                from_statuses=[current["status"]],
                to_status="archived",
            )
        row = await get_vacancy(user_id, pipeline_id)
        return row, {"archived": True, "already_responded": False}

    if full.get("archived") and current["status"] != "sent":
        await asyncio.to_thread(
            vacancy_pipeline.transition,
            user_id=user_id,
            pipeline_id=pipeline_id,
            from_statuses=[current["status"]],
            to_status="archived",
        )

    description = str(full.get("description") or "").strip()
    if not description:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="HH vacancy description was not found; enrichment contract changed",
        )

    await asyncio.to_thread(
        vacancy_pipeline.persist_discovered,
        user_id=user_id,
        resume_id=current.get("resume_id"),
        vacancy=full,
        source_id=None,
    )
    row = await get_vacancy(user_id, pipeline_id)
    return row, {
        "archived": bool(full.get("archived")),
        "already_responded": bool(full.get("already_responded")),
    }
