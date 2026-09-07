import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_manual_run_executes_discovery_then_scoring_only():
    from app.api import search_sources as api

    discovery = {"sources": 2, "fetched": 5, "persisted": 3, "errors": 0}
    scoring = {
        "found": 3,
        "scored": 2,
        "hard_filtered": 1,
        "archived": 0,
        "errors": 0,
        "skipped": 0,
    }
    api._manual_run_locks.pop("u1", None)
    with (
        patch.object(api.source_discovery, "discover_user", new=AsyncMock(return_value=discovery)) as discover,
        patch.object(api.pipeline_scoring, "score_user", new=AsyncMock(return_value=scoring)) as score,
    ):
        result = await api.run_now("u1")

    assert result.discovery == discovery
    assert result.scoring == scoring
    discover.assert_awaited_once_with("u1")
    score.assert_awaited_once_with("u1")


@pytest.mark.asyncio
async def test_manual_run_rejects_second_parallel_click():
    from app.api import search_sources as api

    lock = asyncio.Lock()
    await lock.acquire()
    api._manual_run_locks["u1"] = lock
    try:
        with pytest.raises(HTTPException) as ex:
            await api.run_now("u1")
    finally:
        lock.release()
        api._manual_run_locks.pop("u1", None)

    assert ex.value.status_code == 409
    assert "already in progress" in str(ex.value.detail)
