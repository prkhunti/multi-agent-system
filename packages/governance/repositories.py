"""Persistence contracts for governed enterprise actions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.persistence.models import (
    AuditEventRecord,
    GovernedActionRecord,
    OutboxMessageRecord,
)
from packages.schemas.actions import ActionStatus, GovernedAction
from packages.schemas.audit import AuditEvent, AuditEventCreate
from packages.schemas.outbox import OutboxMessageCreate

AuditWriter = Callable[[AuditEventCreate], Awaitable[AuditEvent]]


class ActionNotFoundError(LookupError):
    """Raised when a governed action does not exist."""


class ActionTransitionConflictError(RuntimeError):
    """Raised when another request already changed an action's state."""


class ActionRepository(Protocol):
    """Persistence operations required by the governance service."""

    uses_transactional_outbox: bool

    async def create_proposal(
        self,
        action: GovernedAction,
        audit_event: AuditEventCreate,
        outbox_message: OutboxMessageCreate,
    ) -> tuple[GovernedAction, bool]:
        """Atomically create an action, audit event, and workflow message."""
        ...

    async def get(self, action_id: UUID) -> GovernedAction:
        """Return an action by identifier."""
        ...

    async def save_transition(
        self,
        action: GovernedAction,
        audit_event: AuditEventCreate,
        *,
        expected_status: ActionStatus,
    ) -> GovernedAction:
        """Atomically persist an action transition and audit event."""
        ...


class InMemoryActionRepository:
    """Concurrency-safe action repository for tests and local development."""

    uses_transactional_outbox = False

    def __init__(self, audit_writer: AuditWriter | None = None) -> None:
        self._actions: dict[UUID, GovernedAction] = {}
        self._idempotency: dict[tuple[str, UUID], UUID] = {}
        self._audit_writer = audit_writer
        self._lock = asyncio.Lock()

    async def create_proposal(
        self,
        action: GovernedAction,
        audit_event: AuditEventCreate,
        outbox_message: OutboxMessageCreate,
    ) -> tuple[GovernedAction, bool]:
        """Create an action and audit record or return its idempotent predecessor."""
        del outbox_message
        key = (action.tenant_id, action.idempotency_key)
        async with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                return self._actions[existing_id], False
            self._actions[action.id] = action
            self._idempotency[key] = action.id
            try:
                if self._audit_writer is not None:
                    await self._audit_writer(audit_event)
            except Exception:
                self._actions.pop(action.id, None)
                self._idempotency.pop(key, None)
                raise
        return action, True

    async def get(self, action_id: UUID) -> GovernedAction:
        """Return an action by identifier."""
        try:
            return self._actions[action_id]
        except KeyError as exc:
            raise ActionNotFoundError(f"Governed action {action_id} was not found") from exc

    async def save_transition(
        self,
        action: GovernedAction,
        audit_event: AuditEventCreate,
        *,
        expected_status: ActionStatus,
    ) -> GovernedAction:
        """Persist a state transition and its audit event as one memory operation."""
        async with self._lock:
            current = self._actions.get(action.id)
            if current is None:
                raise ActionNotFoundError(f"Governed action {action.id} was not found")
            if current.status is not expected_status:
                raise ActionTransitionConflictError(
                    f"Action {action.id} is already {current.status.value}"
                )
            self._actions[action.id] = action
            try:
                if self._audit_writer is not None:
                    await self._audit_writer(audit_event)
            except Exception:
                self._actions[action.id] = current
                raise
        return action


class SqlAlchemyActionRepository:
    """PostgreSQL-backed governed action repository with atomic audit writes."""

    uses_transactional_outbox = True

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_proposal(
        self,
        action: GovernedAction,
        audit_event: AuditEventCreate,
        outbox_message: OutboxMessageCreate,
    ) -> tuple[GovernedAction, bool]:
        """Atomically create an action, audit event, and outbox message."""
        existing = await self._get_by_idempotency(action.tenant_id, action.idempotency_key)
        if existing is not None:
            return existing, False

        now = datetime.now(UTC)
        async with self._sessions() as session:
            session.add_all(
                [
                    self._record_from_action(action),
                    self._audit_record(audit_event, now),
                    OutboxMessageRecord(
                        id=uuid4(),
                        aggregate_type=outbox_message.aggregate_type,
                        aggregate_id=outbox_message.aggregate_id,
                        event_type=outbox_message.event_type.value,
                        payload_json=outbox_message.payload.model_dump(mode="json"),
                        attempt_count=0,
                        available_at=now,
                        created_at=now,
                    ),
                ]
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._get_by_idempotency(
                    action.tenant_id,
                    action.idempotency_key,
                )
                if existing is None:
                    raise
                return existing, False
        return action, True

    async def get(self, action_id: UUID) -> GovernedAction:
        """Return an action by identifier."""
        async with self._sessions() as session:
            record = await session.get(GovernedActionRecord, action_id)
        if record is None:
            raise ActionNotFoundError(f"Governed action {action_id} was not found")
        return self._action_from_record(record)

    async def save_transition(
        self,
        action: GovernedAction,
        audit_event: AuditEventCreate,
        *,
        expected_status: ActionStatus,
    ) -> GovernedAction:
        """Atomically persist an action transition and append its audit event."""
        async with self._sessions() as session, session.begin():
            record = await session.get(
                GovernedActionRecord,
                action.id,
                with_for_update=True,
            )
            if record is None:
                raise ActionNotFoundError(f"Governed action {action.id} was not found")
            if record.status != expected_status.value:
                raise ActionTransitionConflictError(
                    f"Action {action.id} is already {record.status}"
                )
            record.status = action.status.value
            record.approver_id = action.approver_id
            record.approval_comment = action.approval_comment
            record.execution_receipt_json = (
                action.execution_receipt.model_dump(mode="json")
                if action.execution_receipt is not None
                else None
            )
            record.decided_at = action.decided_at
            record.executed_at = action.executed_at
            session.add(self._audit_record(audit_event, datetime.now(UTC)))
        return action

    async def _get_by_idempotency(
        self,
        tenant_id: str,
        idempotency_key: UUID,
    ) -> GovernedAction | None:
        statement = select(GovernedActionRecord).where(
            GovernedActionRecord.tenant_id == tenant_id,
            GovernedActionRecord.idempotency_key == idempotency_key,
        )
        async with self._sessions() as session:
            record = (await session.scalars(statement)).one_or_none()
        return self._action_from_record(record) if record is not None else None

    def _audit_record(
        self,
        event: AuditEventCreate,
        created_at: datetime,
    ) -> AuditEventRecord:
        return AuditEventRecord(
            id=uuid4(),
            case_id=event.case_id,
            event_type=event.event_type,
            actor_id=event.actor_id,
            payload_json=event.payload,
            created_at=created_at,
        )

    def _record_from_action(self, action: GovernedAction) -> GovernedActionRecord:
        return GovernedActionRecord(
            id=action.id,
            case_id=action.case_id,
            review_id=action.review_id,
            tenant_id=action.tenant_id,
            tool_name=action.tool_name,
            arguments_json=action.arguments.model_dump(mode="json"),
            status=action.status.value,
            proposer_id=action.proposer_id,
            approver_id=action.approver_id,
            approval_comment=action.approval_comment,
            idempotency_key=action.idempotency_key,
            workflow_execution_arn=action.workflow_execution_arn,
            execution_receipt_json=None,
            created_at=action.created_at,
            decided_at=action.decided_at,
            executed_at=action.executed_at,
        )

    def _action_from_record(self, record: GovernedActionRecord) -> GovernedAction:
        return GovernedAction(
            id=record.id,
            case_id=record.case_id,
            review_id=record.review_id,
            tenant_id=record.tenant_id,
            tool_name=record.tool_name,
            arguments=record.arguments_json,
            status=record.status,
            proposer_id=record.proposer_id,
            approver_id=record.approver_id,
            approval_comment=record.approval_comment,
            idempotency_key=record.idempotency_key,
            workflow_execution_arn=record.workflow_execution_arn,
            execution_receipt=record.execution_receipt_json,
            created_at=record.created_at,
            decided_at=record.decided_at,
            executed_at=record.executed_at,
        )
