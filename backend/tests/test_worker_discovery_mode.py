from unittest.mock import AsyncMock, MagicMock, patch


async def test_worker_flag_starts_discovery_not_legacy_apply():
    import worker_main

    registry = MagicMock()
    registry.active_user_ids.return_value = []
    registry.reconcile = AsyncMock()
    worker_main._next_discovery_at.clear()

    with (
        patch.object(
            worker_main,
            "active_user_flags",
            return_value={"u1": (True, False)},
        ),
        patch.object(worker_main, "filter_paid", return_value=[]),
        patch.object(
            worker_main,
            "discover_user",
            new=AsyncMock(return_value={"sources": 1, "fetched": 2, "persisted": 2, "errors": 0}),
        ) as discover,
    ):
        await worker_main._reconcile(registry)

    discover.assert_awaited_once_with("u1")
    # Critical contract: legacy auto-apply loop is never started by worker_main.
    registry.reconcile.assert_awaited_once_with("u1", False, False)


async def test_discovery_is_not_repeated_on_every_15_second_reconcile():
    import worker_main

    registry = MagicMock()
    registry.active_user_ids.return_value = []
    registry.reconcile = AsyncMock()
    worker_main._next_discovery_at.clear()

    with (
        patch.object(
            worker_main,
            "active_user_flags",
            return_value={"u1": (True, False)},
        ),
        patch.object(worker_main, "filter_paid", return_value=[]),
        patch.object(worker_main.time, "monotonic", side_effect=[100.0, 101.0]),
        patch.object(
            worker_main,
            "discover_user",
            new=AsyncMock(return_value={"sources": 0, "fetched": 0, "persisted": 0, "errors": 0}),
        ) as discover,
    ):
        await worker_main._reconcile(registry)
        await worker_main._reconcile(registry)

    discover.assert_awaited_once_with("u1")
