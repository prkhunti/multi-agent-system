"""Retry-safe delivery of committed outbox messages."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from packages.governance.repositories import ActionRepository
from packages.outbox.repositories import OutboxRepository
from packages.schemas.outbox import (
    ApprovalWorkflowStartPayload,
    OutboxDispatchResult,
    OutboxEventType,
    OutboxMessage,
)
from packages.workflows.approvals import ApprovalWorkflow

logger = logging.getLogger(__name__)


class ApprovalWorkflowStartHandler:
    """Deliver approval workflow starts from persisted action state."""

    def __init__(
        self,
        actions: ActionRepository,
        approval_workflow: ApprovalWorkflow,
    ) -> None:
        self._actions = actions
        self._approval_workflow = approval_workflow

    async def handle(self, message: OutboxMessage) -> dict[str, str]:
        """Start the workflow using the authoritative persisted action.

        Parameters
        ----------
        message : OutboxMessage
            Leased workflow-start message.

        Returns
        -------
        dict[str, str]
            Durable workflow execution reference.
        """
        if message.event_type != OutboxEventType.APPROVAL_WORKFLOW_START:
            raise ValueError(f"Unsupported outbox event type {message.event_type}")
        payload = ApprovalWorkflowStartPayload.model_validate(message.payload)
        action = await self._actions.get(payload.action_id)
        if action.id != message.aggregate_id:
            raise ValueError("Outbox aggregate does not match its action payload")
        reference = await self._approval_workflow.start(action)
        return {"workflow_execution_arn": reference}


class OutboxDispatcher:
    """Claim, deliver, and acknowledge a bounded batch of outbox messages."""

    def __init__(
        self,
        repository: OutboxRepository,
        handler: ApprovalWorkflowStartHandler,
        *,
        worker_id: str,
        batch_size: int = 20,
        lock_timeout: timedelta = timedelta(minutes=2),
        max_attempts: int = 8,
        retry_base: timedelta = timedelta(seconds=2),
        retry_max: timedelta = timedelta(minutes=5),
    ) -> None:
        self._repository = repository
        self._handler = handler
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lock_timeout = lock_timeout
        self._max_attempts = max_attempts
        self._retry_base = retry_base
        self._retry_max = retry_max

    async def dispatch_once(self, *, now: datetime | None = None) -> OutboxDispatchResult:
        """Deliver one batch and return its outcome counters.

        Parameters
        ----------
        now : datetime or None
            Optional UTC clock value for deterministic tests.

        Returns
        -------
        OutboxDispatchResult
            Number of claimed, published, retried, and dead-lettered messages.
        """
        current_time = now or datetime.now(UTC)
        messages = await self._repository.claim_batch(
            worker_id=self._worker_id,
            batch_size=self._batch_size,
            lock_timeout=self._lock_timeout,
            now=current_time,
        )
        published = 0
        retried = 0
        dead_lettered = 0
        for message in messages:
            try:
                delivery_result = await self._handler.handle(message)
            except Exception as exc:
                retry_at = current_time + self._retry_delay(message.attempt_count)
                is_dead_lettered = await self._repository.record_failure(
                    message.id,
                    worker_id=self._worker_id,
                    error_type=type(exc).__name__,
                    retry_at=retry_at,
                    max_attempts=self._max_attempts,
                    now=current_time,
                )
                dead_lettered += int(is_dead_lettered)
                retried += int(not is_dead_lettered)
                logger.warning(
                    "outbox.delivery_failed",
                    extra={
                        "message_id": str(message.id),
                        "event_type": message.event_type.value,
                        "attempt": message.attempt_count + 1,
                        "dead_lettered": is_dead_lettered,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            await self._repository.mark_published(
                message.id,
                worker_id=self._worker_id,
                delivery_result=delivery_result,
                now=current_time,
            )
            published += 1
            logger.info(
                "outbox.published",
                extra={
                    "message_id": str(message.id),
                    "event_type": message.event_type.value,
                    "attempt": message.attempt_count + 1,
                },
            )
        return OutboxDispatchResult(
            claimed=len(messages),
            published=published,
            retried=retried,
            dead_lettered=dead_lettered,
        )

    def _retry_delay(self, attempt_count: int) -> timedelta:
        multiplier = 2 ** min(attempt_count, 20)
        seconds = min(
            self._retry_base.total_seconds() * multiplier,
            self._retry_max.total_seconds(),
        )
        return timedelta(seconds=seconds)
