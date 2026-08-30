"""Failure-injection tests for the transactional outbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from packages.governance.repositories import SqlAlchemyActionRepository
from packages.governance.service import GovernanceService, InMemorySupplierSystem
from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.model_gateway.deterministic import DeterministicBackend
from packages.outbox.dispatcher import ApprovalWorkflowStartHandler, OutboxDispatcher
from packages.outbox.repositories import SqlAlchemyOutboxRepository
from packages.persistence.database import create_session_factory
from packages.persistence.models import (
    AuditEventRecord,
    Base,
    GovernedActionRecord,
    OutboxMessageRecord,
)
from packages.persistence.repositories import SqlAlchemyCaseRepository
from packages.schemas.actions import ActionProposalRequest, GovernedAction
from packages.schemas.cases import SupplierCase
from packages.schemas.identity import Principal, Role
from packages.schemas.outbox import OutboxEventType, OutboxStatus
from packages.workflows.approvals import LocalApprovalWorkflow


class _RetryingWorkflow:
    def __init__(self, failures: int = 0) -> None:
        self._failures = failures
        self.calls = 0

    async def start(self, action: GovernedAction) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise RuntimeError("synthetic workflow failure")
        return f"test://approval/{action.id}"


async def _database() -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    return engine, create_session_factory(engine)


async def _reviewed_case(
    sessions: async_sessionmaker[AsyncSession],
    supplier_case: SupplierCase,
) -> None:
    cases = SqlAlchemyCaseRepository(sessions)
    await cases.create(supplier_case)
    review = await SupplierReviewWorkflow(DeterministicBackend()).run(supplier_case)
    await cases.save_review(review)


async def _propose(
    sessions: async_sessionmaker[AsyncSession],
    supplier_case: SupplierCase,
    *,
    idempotency_key: UUID | None = None,
) -> tuple[GovernedAction, SqlAlchemyActionRepository]:
    cases = SqlAlchemyCaseRepository(sessions)
    actions = SqlAlchemyActionRepository(sessions)
    service = GovernanceService(
        repository=actions,
        approval_workflow=LocalApprovalWorkflow(),
        supplier_system=InMemorySupplierSystem(),
    )
    action = await service.propose(
        supplier_case=supplier_case,
        review=await cases.get_latest_review(supplier_case.id),
        request=ActionProposalRequest(idempotency_key=idempotency_key or uuid4()),
        principal=Principal(
            subject="analyst@example.com",
            tenant_id=supplier_case.tenant_id,
            roles=frozenset({Role.ANALYST}),
        ),
    )
    return action, actions


async def test_proposal_rolls_back_when_outbox_enqueue_fails(
    risky_supplier_case: SupplierCase,
) -> None:
    engine, sessions = await _database()
    await _reviewed_case(sessions, risky_supplier_case)
    idempotency_key = uuid4()
    action_id = uuid5(
        NAMESPACE_URL,
        f"{risky_supplier_case.tenant_id}:{idempotency_key}",
    )
    now = datetime.now(UTC)
    async with sessions() as session:
        session.add(
            OutboxMessageRecord(
                id=uuid4(),
                aggregate_type="governed_action",
                aggregate_id=action_id,
                event_type=OutboxEventType.APPROVAL_WORKFLOW_START.value,
                payload_json={
                    "action_id": str(action_id),
                    "case_id": str(risky_supplier_case.id),
                    "tenant_id": risky_supplier_case.tenant_id,
                },
                attempt_count=0,
                available_at=now,
                created_at=now,
            )
        )
        await session.commit()

    with pytest.raises(IntegrityError):
        await _propose(
            sessions,
            risky_supplier_case,
            idempotency_key=idempotency_key,
        )

    async with sessions() as session:
        action_count = await session.scalar(select(func.count()).select_from(GovernedActionRecord))
        audit_count = await session.scalar(select(func.count()).select_from(AuditEventRecord))
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxMessageRecord))
    assert action_count == 0
    assert audit_count == 0
    assert outbox_count == 1
    await engine.dispose()


async def test_failed_delivery_retries_and_recovers(
    risky_supplier_case: SupplierCase,
) -> None:
    engine, sessions = await _database()
    await _reviewed_case(sessions, risky_supplier_case)
    action, actions = await _propose(sessions, risky_supplier_case)
    outbox = SqlAlchemyOutboxRepository(sessions)
    workflow = _RetryingWorkflow(failures=1)
    dispatcher = OutboxDispatcher(
        outbox,
        ApprovalWorkflowStartHandler(actions, workflow),
        worker_id="worker-retry",
        retry_base=timedelta(seconds=2),
    )
    now = datetime.now(UTC) + timedelta(seconds=1)

    first = await dispatcher.dispatch_once(now=now)
    too_early = await dispatcher.dispatch_once(now=now + timedelta(seconds=1))
    recovered = await dispatcher.dispatch_once(now=now + timedelta(seconds=2))

    assert first.model_dump() == {
        "claimed": 1,
        "published": 0,
        "retried": 1,
        "dead_lettered": 0,
    }
    assert too_early.claimed == 0
    assert recovered.published == 1
    assert workflow.calls == 2
    stored_action = await actions.get(action.id)
    assert stored_action.workflow_execution_arn == f"test://approval/{action.id}"
    async with sessions() as session:
        record = (await session.scalars(select(OutboxMessageRecord))).one()
    message = await outbox.get(record.id)
    assert message.status is OutboxStatus.PUBLISHED
    assert message.attempt_count == 1
    await engine.dispose()


async def test_stale_lease_replays_idempotent_delivery_after_crash(
    risky_supplier_case: SupplierCase,
) -> None:
    engine, sessions = await _database()
    await _reviewed_case(sessions, risky_supplier_case)
    action, actions = await _propose(sessions, risky_supplier_case)
    outbox = SqlAlchemyOutboxRepository(sessions)
    workflow = _RetryingWorkflow()
    handler = ApprovalWorkflowStartHandler(actions, workflow)
    now = datetime.now(UTC) + timedelta(seconds=1)
    leased = await outbox.claim_batch(
        worker_id="crashed-worker",
        batch_size=1,
        lock_timeout=timedelta(seconds=10),
        now=now,
    )
    first_result = await handler.handle(leased[0])
    assert first_result["workflow_execution_arn"] == f"test://approval/{action.id}"

    dispatcher = OutboxDispatcher(
        outbox,
        handler,
        worker_id="recovery-worker",
        lock_timeout=timedelta(seconds=10),
    )
    recovered = await dispatcher.dispatch_once(now=now + timedelta(seconds=11))

    assert recovered.published == 1
    assert workflow.calls == 2
    message = await outbox.get(leased[0].id)
    assert message.status is OutboxStatus.PUBLISHED
    assert message.attempt_count == 0
    assert (await actions.get(action.id)).workflow_execution_arn == f"test://approval/{action.id}"
    await engine.dispose()


async def test_repeated_failure_moves_message_to_dead_letter_state(
    risky_supplier_case: SupplierCase,
) -> None:
    engine, sessions = await _database()
    await _reviewed_case(sessions, risky_supplier_case)
    action, actions = await _propose(sessions, risky_supplier_case)
    outbox = SqlAlchemyOutboxRepository(sessions)
    workflow = _RetryingWorkflow(failures=10)
    dispatcher = OutboxDispatcher(
        outbox,
        ApprovalWorkflowStartHandler(actions, workflow),
        worker_id="worker-dead-letter",
        max_attempts=2,
        retry_base=timedelta(seconds=1),
    )
    now = datetime.now(UTC) + timedelta(seconds=1)

    first = await dispatcher.dispatch_once(now=now)
    second = await dispatcher.dispatch_once(now=now + timedelta(seconds=1))

    assert first.retried == 1
    assert second.dead_lettered == 1
    async with sessions() as session:
        record = (await session.scalars(select(OutboxMessageRecord))).one()
    message = await outbox.get(record.id)
    assert message.status is OutboxStatus.DEAD_LETTERED
    assert message.attempt_count == 2
    assert message.last_error == "RuntimeError"
    assert (await actions.get(action.id)).workflow_execution_arn is None
    await engine.dispose()
