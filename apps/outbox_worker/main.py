"""Poll and deliver committed transactional outbox messages."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from datetime import timedelta

from packages.governance.repositories import SqlAlchemyActionRepository
from packages.outbox.dispatcher import ApprovalWorkflowStartHandler, OutboxDispatcher
from packages.outbox.repositories import SqlAlchemyOutboxRepository
from packages.persistence.database import create_database_engine, create_session_factory
from packages.settings import Settings, get_settings
from packages.workflows.factory import create_approval_workflow

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    """Run the outbox polling loop until the process receives a stop signal."""
    if settings.repository_backend != "postgres":
        raise RuntimeError("The outbox worker requires REPOSITORY_BACKEND=postgres")

    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    actions = SqlAlchemyActionRepository(sessions)
    outbox = SqlAlchemyOutboxRepository(sessions)
    handler = ApprovalWorkflowStartHandler(actions, create_approval_workflow(settings))
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    dispatcher = OutboxDispatcher(
        outbox,
        handler,
        worker_id=worker_id,
        batch_size=settings.outbox_batch_size,
        lock_timeout=timedelta(seconds=settings.outbox_lock_timeout_seconds),
        max_attempts=settings.outbox_max_attempts,
        retry_base=timedelta(seconds=settings.outbox_retry_base_seconds),
        retry_max=timedelta(seconds=settings.outbox_retry_max_seconds),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, stop.set)

    logger.info("outbox.worker_started", extra={"worker_id": worker_id})
    try:
        while not stop.is_set():
            try:
                result = await dispatcher.dispatch_once()
            except Exception as exc:
                logger.error(
                    "outbox.poll_failed",
                    extra={"worker_id": worker_id, "error_type": type(exc).__name__},
                )
                result = None
            if result is not None and result.claimed > 0:
                continue
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=settings.outbox_poll_interval_seconds,
                )
            except TimeoutError:
                pass
    finally:
        await engine.dispose()
        logger.info("outbox.worker_stopped", extra={"worker_id": worker_id})


def main() -> None:
    """Configure logging and start the asynchronous outbox worker."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
