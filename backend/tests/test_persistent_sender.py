from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_sender_kill_switch_returns_before_queue_or_hh_access():
    from app.services import persistent_sender as svc

    with (
        patch.object(svc.settings, "ALLOW_REAL_APPLY", False),
        patch.object(svc, "_next_job") as next_job,
        patch.object(svc.web, "get_vacancy", new=AsyncMock()) as get_vacancy,
        patch.object(svc, "submit_response", new=AsyncMock()) as submit,
    ):
        result = await svc.process_next("u1")

    assert result == {
        "processed": False,
        "outcome": "real_apply_disabled",
        "job_id": None,
    }
    next_job.assert_not_called()
    get_vacancy.assert_not_awaited()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_mismatch_never_calls_submit():
    from app.services import persistent_sender as svc

    job = {
        "id": "q1",
        "vacancy_pipeline_id": "p1",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "approved_letter_hash": svc.letter_hash("approved"),
        "approved_letter_text": "approved",
        "attempts": 0,
    }
    pipeline = {
        "id": "p1",
        "status": "queued_to_send",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "cover_letter_draft": "changed",
        "approved_letter_hash": job["approved_letter_hash"],
    }
    with (
        patch.object(svc.settings, "ALLOW_REAL_APPLY", True),
        patch.object(svc, "_next_job", return_value=job),
        patch.object(svc, "_claim_job", return_value=True),
        patch.object(svc, "_pipeline_snapshot", return_value=pipeline),
        patch.object(svc, "_finish_job") as finish,
        patch.object(svc, "_move_pipeline", new=AsyncMock(return_value=True)) as move,
        patch.object(svc, "submit_response", new=AsyncMock()) as submit,
        patch.object(svc.web, "get_vacancy", new=AsyncMock()) as get_vacancy,
    ):
        result = await svc.process_next("u1")

    assert result["outcome"] == "approval_snapshot_mismatch"
    submit.assert_not_awaited()
    get_vacancy.assert_not_awaited()
    finish.assert_called_once_with("u1", "q1", "failed", "approval_snapshot_mismatch")
    move.assert_awaited_once_with("u1", "p1", "queued_to_send", "send_error")


@pytest.mark.asyncio
async def test_already_responded_is_reconciled_without_submit():
    from app.services import persistent_sender as svc

    text = "approved exact letter"
    digest = svc.letter_hash(text)
    job = {
        "id": "q1",
        "vacancy_pipeline_id": "p1",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "approved_letter_hash": digest,
        "approved_letter_text": text,
        "attempts": 0,
    }
    pipeline = {
        "id": "p1",
        "status": "queued_to_send",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "cover_letter_draft": text,
        "approved_letter_hash": digest,
    }
    with (
        patch.object(svc.settings, "ALLOW_REAL_APPLY", True),
        patch.object(svc, "_next_job", return_value=job),
        patch.object(svc, "_claim_job", return_value=True),
        patch.object(svc, "_pipeline_snapshot", return_value=pipeline),
        patch.object(svc.web, "get_vacancy", new=AsyncMock(return_value={"already_responded": True})),
        patch.object(svc, "_finish_job") as finish,
        patch.object(svc, "_move_pipeline", new=AsyncMock(return_value=True)) as move,
        patch.object(svc, "submit_response", new=AsyncMock()) as submit,
    ):
        result = await svc.process_next("u1")

    assert result["outcome"] == "already_responded"
    submit.assert_not_awaited()
    finish.assert_called_once_with("u1", "q1", "sent", "reconciled_already_responded")
    move.assert_awaited_once_with("u1", "p1", "queued_to_send", "sent")


@pytest.mark.asyncio
async def test_has_test_becomes_manual_required_without_submit():
    from app.services import persistent_sender as svc

    text = "approved exact letter"
    digest = svc.letter_hash(text)
    job = {
        "id": "q1",
        "vacancy_pipeline_id": "p1",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "approved_letter_hash": digest,
        "approved_letter_text": text,
        "attempts": 0,
    }
    pipeline = {
        "id": "p1",
        "status": "queued_to_send",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "cover_letter_draft": text,
        "approved_letter_hash": digest,
    }
    with (
        patch.object(svc.settings, "ALLOW_REAL_APPLY", True),
        patch.object(svc, "_next_job", return_value=job),
        patch.object(svc, "_claim_job", return_value=True),
        patch.object(svc, "_pipeline_snapshot", return_value=pipeline),
        patch.object(svc.web, "get_vacancy", new=AsyncMock(return_value={"has_test": True})),
        patch.object(svc, "_finish_job") as finish,
        patch.object(svc, "_move_pipeline", new=AsyncMock(return_value=True)) as move,
        patch.object(svc, "submit_response", new=AsyncMock()) as submit,
    ):
        result = await svc.process_next("u1")

    assert result["outcome"] == "manual_required"
    submit.assert_not_awaited()
    finish.assert_called_once_with("u1", "q1", "manual_required", "hh_vacancy_has_test")
    move.assert_awaited_once_with("u1", "p1", "queued_to_send", "send_error")


@pytest.mark.asyncio
async def test_sender_passes_exact_approved_snapshot_to_submit():
    from app.services import persistent_sender as svc

    text = "exact approved text with spacing preserved"
    digest = svc.letter_hash(text)
    job = {
        "id": "q1",
        "vacancy_pipeline_id": "p1",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "approved_letter_hash": digest,
        "approved_letter_text": text,
        "attempts": 0,
    }
    pipeline = {
        "id": "p1",
        "status": "queued_to_send",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "cover_letter_draft": text,
        "approved_letter_hash": digest,
    }
    with (
        patch.object(svc.settings, "ALLOW_REAL_APPLY", True),
        patch.object(svc, "_next_job", return_value=job),
        patch.object(svc, "_claim_job", return_value=True),
        patch.object(svc, "_pipeline_snapshot", return_value=pipeline),
        patch.object(svc.web, "get_vacancy", new=AsyncMock(return_value={"archived": False, "already_responded": False, "has_test": False})),
        patch.object(svc, "_increment_attempt"),
        patch.object(svc, "_finish_job"),
        patch.object(svc, "_move_pipeline", new=AsyncMock(return_value=True)),
        patch.object(svc, "submit_response", new=AsyncMock(return_value=("sent", None))) as submit,
    ):
        result = await svc.process_next("u1")

    assert result["outcome"] == "sent"
    submit.assert_awaited_once_with(
        user_id="u1",
        resume_id="r1",
        vacancy_id="123",
        letter=text,
        answers=None,
    )


@pytest.mark.asyncio
async def test_uncertain_submit_is_reconciled_before_failure():
    from app.services import persistent_sender as svc

    text = "approved exact letter"
    digest = svc.letter_hash(text)
    job = {
        "id": "q1",
        "vacancy_pipeline_id": "p1",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "approved_letter_hash": digest,
        "approved_letter_text": text,
        "attempts": 0,
    }
    pipeline = {
        "id": "p1",
        "status": "queued_to_send",
        "resume_id": "r1",
        "hh_vacancy_id": "123",
        "cover_letter_draft": text,
        "approved_letter_hash": digest,
    }
    get_vacancy = AsyncMock(
        side_effect=[
            {"archived": False, "already_responded": False, "has_test": False},
            {"already_responded": True},
        ]
    )
    with (
        patch.object(svc.settings, "ALLOW_REAL_APPLY", True),
        patch.object(svc, "_next_job", return_value=job),
        patch.object(svc, "_claim_job", return_value=True),
        patch.object(svc, "_pipeline_snapshot", return_value=pipeline),
        patch.object(svc.web, "get_vacancy", new=get_vacancy),
        patch.object(svc, "_increment_attempt"),
        patch.object(svc, "_finish_job") as finish,
        patch.object(svc, "_move_pipeline", new=AsyncMock(return_value=True)) as move,
        patch.object(svc, "submit_response", new=AsyncMock(return_value=("failed", "network_timeout"))),
    ):
        result = await svc.process_next("u1")

    assert result["outcome"] == "sent_reconciled"
    assert get_vacancy.await_count == 2
    finish.assert_called_once_with("u1", "q1", "sent", "reconciled_after_uncertain_submit")
    move.assert_awaited_once_with("u1", "p1", "queued_to_send", "sent")
