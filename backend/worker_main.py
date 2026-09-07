"""Standalone worker entrypoint for systemd / docker.

`profiles.worker_enabled` controls persistent vacancy discovery + scoring, not
immediate apply. Discovery writes into `vacancy_pipeline`; scoring enriches the
vacancy over the authenticated HH web session and persists a structured score.
Real sending is a separate future worker fed exclusively by approved send jobs.

The recruiter agent remains an independent paid/autonomous loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from dotenv import load_dotenv

load_dotenv()

from app.services.pipeline_scoring import score_user
from app.services.plan import filter_paid
from app.services.source_discovery import discover_user
from app.services.worker_control import active_user_flags
from app.worker.runner import get_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("worker_main")

POLL_INTERVAL_S = 15
DISCOVERY_INTERVAL_S = 5 * 60
_next_discovery_at: dict[str, float] = {}


async def _run_discovery_if_due(user_id: str, enabled: bool) -> None:
    if not enabled:
        _next_discovery_at.pop(user_id, None)
        return
    now = time.monotonic()
    if now < _next_discovery_at.get(user_id, 0.0):
        return
    # Set the next slot before starting. If this run fails, we still avoid a
    # 15-second retry storm against HH; the source stores its own last_error.
    _next_discovery_at[user_id] = now + DISCOVERY_INTERVAL_S
    try:
        discovery_summary = await discover_user(user_id)
    except Exception:
        logger.exception("discovery failed for user=%s", user_id)
        return
    logger.info("discovery complete user=%s summary=%s", user_id, discovery_summary)

    # Scoring is sequential and capped per run. It has no send side effects and
    # is fail-closed: enrichment/LLM errors become score_error, never auto-keep.
    try:
        scoring_summary = await score_user(user_id)
    except Exception:
        logger.exception("scoring batch failed for user=%s", user_id)
        return
    logger.info("scoring complete user=%s summary=%s", user_id, scoring_summary)


async def _reconcile(registry) -> None:
    loop = asyncio.get_running_loop()
    flags = await loop.run_in_executor(None, active_user_flags)
    paid = set(await loop.run_in_executor(None, filter_paid, list(flags.keys())))

    # Existing flag tuple is (worker_enabled, agent_enabled). During migration
    # worker_enabled is reinterpreted as discovery/scoring enabled. The legacy
    # apply runner is NEVER started from worker_main; sending gets its own queue.
    desired_agent: dict[str, bool] = {}
    for uid, (discovery_on, agent_on) in flags.items():
        desired_agent[uid] = agent_on and uid in paid
        await _run_discovery_if_due(uid, discovery_on)

    # A recruiter loop can outlive the flag row between reconciles; stop it if
    # the user is no longer desired. Apply is always false here by construction.
    for uid in registry.active_user_ids():
        desired_agent.setdefault(uid, False)

    for uid, agent_on in desired_agent.items():
        logger.info(
            "reconcile: user=%s discovery=%s agent=%s",
            uid,
            bool(flags.get(uid, (False, False))[0]),
            agent_on,
        )
        await registry.reconcile(uid, False, agent_on)


async def main() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(signame: str) -> None:
        logger.info("received %s — initiating shutdown", signame)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop, sig.name)

    registry = get_registry()
    logger.info(
        "worker_main: discovery/scoring/recruiter reconcile loop start (every %ds)",
        POLL_INTERVAL_S,
    )
    while not stop_event.is_set():
        try:
            await _reconcile(registry)
        except Exception:
            logger.exception("reconcile failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_S)
        except TimeoutError:
            pass

    logger.info("stopping recruiter/legacy runners")
    await registry.stop_all()
    logger.info("worker_main shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
