from unittest.mock import AsyncMock, MagicMock, patch


async def test_discovery_stops_when_previous_head_is_reached():
    from app.services import source_discovery as sd

    source = {
        "id": "s1",
        "resume_id": "r1",
        "query_pairs": [{"key": "text", "value": "CIO"}],
        "cursor": {"head_ids": ["old-1", "old-2"]},
    }
    page = [
        {"id": "new-1", "name": "CIO", "employer": {"id": "1", "name": "A"}},
        {"id": "old-1", "name": "CIO old", "employer": {"id": "2", "name": "B"}},
        {"id": "older", "name": "older", "employer": {"id": "3", "name": "C"}},
    ]
    persisted: list[str] = []
    source_updates: list[dict] = []

    def fake_persist(**kwargs):
        persisted.append(str(kwargs["vacancy"]["id"]))
        return {"id": "p1"}

    def fake_update(source_id, **changes):
        assert source_id == "s1"
        source_updates.append(changes)

    with (
        patch.object(sd, "_search_page", new=AsyncMock(return_value=(page, 100))),
        patch.object(sd, "_existing_application_ids", return_value=set()),
        patch.object(sd, "persist_discovered", side_effect=fake_persist),
        patch.object(sd, "_update_source", side_effect=fake_update),
    ):
        result = await sd.discover_source("u1", source)

    assert persisted == ["new-1"]
    assert result["overlap_found"] is True
    assert result["persisted"] == 1
    final = source_updates[-1]
    assert final["cursor"]["head_ids"] == ["new-1", "old-1", "older"]
    assert final["last_error"] is None


def test_request_pairs_keep_duplicate_hh_parameters():
    from app.services.source_discovery import _request_pairs

    source = {
        "query_pairs": [
            {"key": "area", "value": "1"},
            {"key": "area", "value": "2"},
            {"key": "search_field", "value": "name"},
            {"key": "search_field", "value": "company_name"},
            {"key": "page", "value": "9"},
            {"key": "order_by", "value": "relevance"},
        ]
    }

    assert _request_pairs(source, 3) == [
        ("area", "1"),
        ("area", "2"),
        ("search_field", "name"),
        ("search_field", "company_name"),
        ("order_by", "publication_time"),
        ("page", "3"),
    ]
