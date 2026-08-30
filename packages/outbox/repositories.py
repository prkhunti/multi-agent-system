"""Transactional outbox repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.persistence.models import GovernedActionRecord, OutboxMessageRecord
from packages.schemas.outbox import (
    ApprovalWorkflowStartPayload,
    OutboxEventType,
    OutboxMessage,
    OutboxStatus,
)


class OutboxMessageNotFoundError(LookupError):
    """Raised when an outbox message does not exist."""


class OutboxLeaseLostError(RuntimeError):
    """Raised when a worker no longer owns an outbox message lease."""


class OutboxRepository(Protocol):
    """Persistence operations required by the outbox dispatcher."""

    async def claim_batch(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lock_timeout: timedelta,
        now: datetime,
    ) -> list[OutboxMessage]:
        """Lease a bounded set of deliverable messages."""
        ...

    async def mark_published(
        self,
        message_id: UUID,
        *,
        worker_id: str,
        delivery_result: dict[str, str],
        now: datetime,
    ) -> None:
        """Atomically persist the delivery result and mark the message published."""
        ...

    async def record_failure(
        self,
        message_id: UUID,
        *,
        worker_id: str,
        error_type: str,
        retry_at: datetime,
        max_attempts: int,
        now: datetime,
    ) -> bool:
        """Release a failed message for retry or dead-letter it."""
        ...

    async def get(self, message_id: UUID) -> OutboxMessage:
        """Return a message for operational inspection."""
        ...


class SqlAlchemyOutboxRepository:
    """PostgreSQL outbox using short leases and SKIP LOCKED claims."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim_batch(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lock_timeout: timedelta,
        now: datetime,
    ) -> list[OutboxMessage]:
        """Lease due messages without holding locks during external calls.

        Parameters
        ----------
        worker_id : str
            Stable identifier for the dispatcher process.
        batch_size : int
            Maximum number of messages to claim.
        lock_timeout : timedelta
            Age after which an abandoned lease can be reclaimed.
        now : datetime
            Current UTC time supplied by the dispatcher.

        Returns
        -------
        list[OutboxMessage]
            Messages leased to the requesting worker.
        """
        stale_before = now - lock_timeout
        statement = (
            select(OutboxMessageRecord)
            .where(
                OutboxMessageRecord.published_at.is_(None),
                OutboxMessageRecord.dead_lettered_at.is_(None),
                OutboxMessageRecord.available_at <= now,
                or_(
                    OutboxMessageRecord.locked_at.is_(None),
                    OutboxMessageRecord.locked_at <= stale_before,
                ),
            )
            .order_by(OutboxMessageRecord.available_at, OutboxMessageRecord.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        async with self._sessions() as session, session.begin():
            records = list((await session.scalars(statement)).all())
            for record in records:
                record.locked_at = now
                record.locked_by = worker_id
        return [self._message_from_record(record) for record in records]

    async def mark_published(
        self,
        message_id: UUID,
        *,
        worker_id: str,
        delivery_result: dict[str, str],
        now: datetime,
    ) -> None:
        """Persist the workflow reference and publication marker atomically."""
        async with self._sessions() as session, session.begin():
            record = await session.get(
                OutboxMessageRecord,
                message_id,
                with_for_update=True,
            )
            if record is None:
                raise OutboxMessageNotFoundError(f"Outbox message {message_id} was not found")
            if record.published_at is not None:
                return
            self._require_lease(record, worker_id)
            if record.event_type != OutboxEventType.APPROVAL_WORKFLOW_START.value:
                raise ValueError(f"Unsupported outbox event type {record.event_type}")
            workflow_reference = delivery_result.get("workflow_execution_arn")
            if not workflow_reference:
                raise ValueError("Workflow delivery did not return an execution reference")
            payload = ApprovalWorkflowStartPayload.model_validate(record.payload_json)
            action = await session.get(
                GovernedActionRecord,
                payload.action_id,
                with_for_update=True,
            )
            if action is None:
                raise ValueError(f"Governed action {payload.action_id} was not found")
            action.workflow_execution_arn = workflow_reference
            record.published_at = now
            record.locked_at = None
            record.locked_by = None
            record.last_error = None
            record.delivery_result_json = delivery_result

    async def record_failure(
        self,
        message_id: UUID,
        *,
        worker_id: str,
        error_type: str,
        retry_at: datetime,
        max_attempts: int,
        now: datetime,
    ) -> bool:
        """Release a failed message or move it to the dead-letter state."""
        async with self._sessions() as session, session.begin():
            record = await session.get(
                OutboxMessageRecord,
                message_id,
                with_for_update=True,
            )
            if record is None:
                raise OutboxMessageNotFoundError(f"Outbox message {message_id} was not found")
            self._require_lease(record, worker_id)
            record.attempt_count += 1
            record.last_error = error_type[:255]
            record.locked_at = None
            record.locked_by = None
            if record.attempt_count >= max_attempts:
                record.dead_lettered_at = now
                return True
            record.available_at = retry_at
            return False

    async def get(self, message_id: UUID) -> OutboxMessage:
        """Return one outbox message."""
        async with self._sessions() as session:
            record = await session.get(OutboxMessageRecord, message_id)
        if record is None:
            raise OutboxMessageNotFoundError(f"Outbox message {message_id} was not found")
        return self._message_from_record(record)

    def _require_lease(self, record: OutboxMessageRecord, worker_id: str) -> None:
        if record.locked_by != worker_id:
            raise OutboxLeaseLostError(
                f"Worker {worker_id} no longer owns outbox message {record.id}"
            )

    def _message_from_record(self, record: OutboxMessageRecord) -> OutboxMessage:
        status = OutboxStatus.PENDING
        if record.published_at is not None:
            status = OutboxStatus.PUBLISHED
        elif record.dead_lettered_at is not None:
            status = OutboxStatus.DEAD_LETTERED
        return OutboxMessage(
            id=record.id,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            event_type=record.event_type,
            payload=record.payload_json,
            status=status,
            attempt_count=record.attempt_count,
            available_at=self._as_utc(record.available_at),
            locked_at=self._as_utc(record.locked_at),
            locked_by=record.locked_by,
            published_at=self._as_utc(record.published_at),
            dead_lettered_at=self._as_utc(record.dead_lettered_at),
            last_error=record.last_error,
            delivery_result=record.delivery_result_json,
            created_at=self._as_utc(record.created_at),
        )

    def _as_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
