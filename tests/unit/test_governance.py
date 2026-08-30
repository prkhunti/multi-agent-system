"""Tests for governed enterprise action policy."""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.governance.repositories import InMemoryActionRepository
from packages.governance.service import (
    ActionStateError,
    AuthorizationDeniedError,
    GovernanceService,
    InMemorySupplierSystem,
)
from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.model_gateway.deterministic import DeterministicBackend
from packages.schemas.actions import (
    ActionProposalRequest,
    ActionStatus,
    ApprovalDecision,
    ApprovalRequest,
)
from packages.schemas.cases import SupplierCase
from packages.schemas.identity import Principal, Role
from packages.workflows.approvals import LocalApprovalWorkflow


def _principal(subject: str, role: Role, tenant_id: str = "tenant-test") -> Principal:
    return Principal(subject=subject, tenant_id=tenant_id, roles=frozenset({role}))


async def test_governed_action_requires_independent_approval_and_is_idempotent(
    risky_supplier_case: SupplierCase,
) -> None:
    repository = InMemoryActionRepository()
    service = GovernanceService(
        repository=repository,
        approval_workflow=LocalApprovalWorkflow(),
        supplier_system=InMemorySupplierSystem(),
    )
    review = await SupplierReviewWorkflow(DeterministicBackend()).run(risky_supplier_case)
    analyst = _principal("analyst@example.com", Role.ANALYST)
    request = ActionProposalRequest(idempotency_key=uuid4())

    proposed = await service.propose(
        supplier_case=risky_supplier_case,
        review=review,
        request=request,
        principal=analyst,
    )
    replayed = await service.propose(
        supplier_case=risky_supplier_case,
        review=review,
        request=request,
        principal=analyst,
    )

    assert proposed.id == replayed.id
    assert proposed.status is ActionStatus.PENDING_APPROVAL
    assert proposed.arguments.decision == review.recommendation.decision

    with pytest.raises(AuthorizationDeniedError, match="own action"):
        await service.decide(
            action_id=proposed.id,
            request=ApprovalRequest(decision=ApprovalDecision.APPROVE),
            principal=Principal(
                subject=analyst.subject,
                tenant_id=analyst.tenant_id,
                roles=frozenset({Role.APPROVER}),
            ),
        )

    with pytest.raises(AuthorizationDeniedError, match="another tenant"):
        await service.get(
            proposed.id,
            _principal("other@example.com", Role.APPROVER, "tenant-other"),
        )

    approved = await service.decide(
        action_id=proposed.id,
        request=ApprovalRequest(
            decision=ApprovalDecision.APPROVE,
            comment="Risk committee approved",
        ),
        principal=_principal("approver@example.com", Role.APPROVER),
    )
    assert approved.status is ActionStatus.APPROVED

    executor = _principal("executor@example.com", Role.EXECUTOR)
    first_receipt = await service.execute(approved.id, executor)
    second_receipt = await service.execute(approved.id, executor)
    assert first_receipt == second_receipt
    assert (await repository.get(approved.id)).status is ActionStatus.EXECUTED


async def test_unapproved_action_cannot_execute(risky_supplier_case: SupplierCase) -> None:
    service = GovernanceService(
        repository=InMemoryActionRepository(),
        approval_workflow=LocalApprovalWorkflow(),
        supplier_system=InMemorySupplierSystem(),
    )
    review = await SupplierReviewWorkflow(DeterministicBackend()).run(risky_supplier_case)
    proposed = await service.propose(
        supplier_case=risky_supplier_case,
        review=review,
        request=ActionProposalRequest(idempotency_key=uuid4()),
        principal=_principal("analyst@example.com", Role.ANALYST),
    )

    with pytest.raises(ActionStateError, match="not approved"):
        await service.execute(
            proposed.id,
            _principal("executor@example.com", Role.EXECUTOR),
        )
