from fastapi import APIRouter, Depends, status

from app.api.deps import get_current_user
from app.schemas.search_sources import (
    ManualSearchRunResponse,
    SearchSourceCreate,
    SearchSourceResponse,
    SearchSourceUpdate,
    SearchURLPreviewRequest,
    SearchURLPreviewResponse,
)
from app.services import pipeline_scoring, search_source_service, source_discovery

router = APIRouter(prefix="/api/search-sources", tags=["search-sources"])


@router.post("/preview-url", response_model=SearchURLPreviewResponse)
async def preview_url(
    body: SearchURLPreviewRequest,
    _: str = Depends(get_current_user),
):
    return SearchURLPreviewResponse(**(await search_source_service.preview_url(body.url)))


@router.post("/run-now", response_model=ManualSearchRunResponse)
async def run_now(user_id: str = Depends(get_current_user)):
    """Run the same safe discovery + scoring path immediately.

    No sender code is imported or invoked here. Discovery is idempotent at the
    persistent pipeline layer; scoring atomically claims discovered rows.
    """
    discovery = await source_discovery.discover_user(user_id)
    scoring = await pipeline_scoring.score_user(user_id)
    return ManualSearchRunResponse(discovery=discovery, scoring=scoring)


@router.get("", response_model=list[SearchSourceResponse])
async def list_all(user_id: str = Depends(get_current_user)):
    rows = await search_source_service.list_sources(user_id)
    return [SearchSourceResponse(**row) for row in rows]


@router.post("", response_model=SearchSourceResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: SearchSourceCreate,
    user_id: str = Depends(get_current_user),
):
    row = await search_source_service.create_source(
        user_id, body.model_dump(exclude_unset=True)
    )
    return SearchSourceResponse(**row)


@router.patch("/{source_id}", response_model=SearchSourceResponse)
async def update(
    source_id: str,
    body: SearchSourceUpdate,
    user_id: str = Depends(get_current_user),
):
    row = await search_source_service.update_source(
        user_id, source_id, body.model_dump(exclude_unset=True)
    )
    return SearchSourceResponse(**row)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(source_id: str, user_id: str = Depends(get_current_user)):
    await search_source_service.delete_source(user_id, source_id)
