"""Integration tests for the durable SQLAlchemy repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.governance.repositories import SqlAlchemyActionRepository
from packages.governance.service import GovernanceService, InMemorySupplierSystem
from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.model_gateway.deterministic import DeterministicBackend
from packages.persistence.database import create_session_factory
from packages.persistence.models import Base
from packages.persistence.repositories import SqlAlchemyCaseRepository
from packages.schemas.actions import ActionProposalRequest, ApprovalDecision, ApprovalRequest
from packages.schemas.audit import AuditEventCreate
from packages.schemas.cases import CaseStatus, DocumentInput, SupplierCase
from packages.schemas.identity import Principal, Role
from packages.workflows.approvals import LocalApprovalWorkflow


async def test_sql_repository_round_trip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)

    repository = SqlAlchemyCaseRepository(create_session_factory(engine))
    now = datetime.now(UTC)
    supplier_case = SupplierCase(
        id=uuid4(),
        tenant_id="tenant-test",
        supplier_name="Durable Supplier",
        description="Persistence test",
        status=CaseStatus.NEW,
        documents=[
            DocumentInput(title="Questionnaire", content="Shared credentials are permitted."),
            DocumentInput(title="Contract", content="Unlimited liability applies."),
        ],
        created_at=now,
        updated_at=now,
    )

    await repository.create(supplier_case)
    loaded = await repository.get(supplier_case.id)
    assert [item.title for item in loaded.documents] == ["Questionnaire", "Contract"]

    result = await SupplierReviewWorkflow(DeterministicBackend()).run(loaded)
    await repository.save_review(result)
    await repository.save_review(result)
    latest = await repository.get_latest_review(supplier_case.id)
    assert latest.review_id == result.review_id
    assert len(latest.findings) == len(result.findings)

    second_result = await SupplierReviewWorkflow(DeterministicBackend()).run(loaded)
    await repository.save_review(second_result)
    latest = await repository.get_latest_review(supplier_case.id)
    assert latest.review_id == second_result.review_id
    assert {item.id for item in result.findings}.isdisjoint(
        item.id for item in second_result.findings
    )

    governance = GovernanceService(
        repository=SqlAlchemyActionRepository(create_session_factory(engine)),
        approval_workflow=LocalApprovalWorkflow(),
        supplier_system=InMemorySupplierSystem(),
    )
    action = await governance.propose(
        supplier_case=loaded,
        review=latest,
        request=ActionProposalRequest(idempotency_key=uuid4()),
        principal=Principal(
            subject="analyst@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.ANALYST}),
        ),
    )
    approved = await governance.decide(
        action_id=action.id,
        request=ApprovalRequest(decision=ApprovalDecision.APPROVE),
        principal=Principal(
            subject="approver@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.APPROVER}),
        ),
    )
    receipt = await governance.execute(
        approved.id,
        Principal(
            subject="executor@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.EXECUTOR}),
        ),
    )
    assert receipt.applied_decision == latest.recommendation.decision

    await repository.append_audit_event(
        AuditEventCreate(
            case_id=supplier_case.id,
            event_type="test.completed",
            actor_id="test-suite",
        )
    )
    events = await repository.list_audit_events(supplier_case.id)
    assert [event.event_type for event in events] == [
        "action.proposed",
        "action.approved",
        "action.executed",
        "test.completed",
    ]
    await engine.dispose()
