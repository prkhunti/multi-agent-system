"""Tests for the parallel supplier review graph."""

from __future__ import annotations

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from packages.graphs.supplier_review import (
    ReviewExecutionNotFoundError,
    SupplierReviewWorkflow,
)
from packages.model_gateway.base import ModelRequest, ModelTask
from packages.model_gateway.deterministic import DeterministicBackend
from packages.schemas.cases import SupplierCase
from packages.schemas.reviews import (
    Decision,
    EvidenceConfirmation,
    EvidenceConfirmationDecision,
    ReviewExecutionStatus,
    RiskCategory,
)


class _CountingBackend(DeterministicBackend):
    def __init__(self) -> None:
        self.calls: dict[ModelTask, int] = {}

    async def generate(self, request: ModelRequest) -> str:
        self.calls[request.task] = self.calls.get(request.task, 0) + 1
        return await super().generate(request)


async def test_review_graph_runs_all_specialists(
    risky_supplier_case: SupplierCase,
) -> None:
    workflow = SupplierReviewWorkflow(DeterministicBackend())

    result = await workflow.run(risky_supplier_case)

    categories = {finding.category for finding in result.findings}
    assert categories == {
        RiskCategory.SECURITY,
        RiskCategory.LEGAL,
        RiskCategory.FINANCIAL,
    }
    assert result.recommendation.decision is Decision.ESCALATE
    assert result.model_backend == "deterministic"


async def test_checkpointed_review_pauses_and_resumes_without_repeating_specialists(
    risky_supplier_case: SupplierCase,
) -> None:
    backend = _CountingBackend()
    checkpointer = InMemorySaver()
    execution_id = uuid4()
    first_process = SupplierReviewWorkflow(backend, checkpointer)

    paused = await first_process.start(
        risky_supplier_case,
        execution_id=execution_id,
        require_evidence_confirmation=True,
    )
    replayed = await first_process.start(
        risky_supplier_case,
        execution_id=execution_id,
        require_evidence_confirmation=True,
    )

    assert paused.status is ReviewExecutionStatus.AWAITING_INPUT
    assert paused.interrupt is not None
    assert paused.interrupt.finding_count == 5
    assert replayed == paused
    assert backend.calls == {
        ModelTask.SECURITY_REVIEW: 1,
        ModelTask.LEGAL_REVIEW: 1,
        ModelTask.FINANCIAL_REVIEW: 1,
    }

    second_process = SupplierReviewWorkflow(backend, checkpointer)
    completed = await second_process.resume(
        risky_supplier_case,
        execution_id,
        EvidenceConfirmation(
            decision=EvidenceConfirmationDecision.CONFIRM,
            comment="Evidence checked against the submitted source documents.",
        ),
    )
    idempotent_resume = await second_process.resume(
        risky_supplier_case,
        execution_id,
        EvidenceConfirmation(
            decision=EvidenceConfirmationDecision.CONFIRM,
            comment="Repeated client delivery.",
        ),
    )

    assert completed.status is ReviewExecutionStatus.COMPLETED
    assert completed.result is not None
    assert completed.result.review_id == execution_id
    assert idempotent_resume == completed
    assert backend.calls == {
        ModelTask.SECURITY_REVIEW: 1,
        ModelTask.LEGAL_REVIEW: 1,
        ModelTask.FINANCIAL_REVIEW: 1,
        ModelTask.SYNTHESIZE: 1,
    }


async def test_checkpoint_scope_hides_execution_from_another_tenant(
    risky_supplier_case: SupplierCase,
) -> None:
    workflow = SupplierReviewWorkflow(DeterministicBackend())
    execution_id = uuid4()
    await workflow.start(
        risky_supplier_case,
        execution_id=execution_id,
        require_evidence_confirmation=True,
    )
    other_tenant_case = risky_supplier_case.model_copy(update={"tenant_id": "tenant-other"})

    with pytest.raises(ReviewExecutionNotFoundError):
        await workflow.get_execution(other_tenant_case, execution_id)
