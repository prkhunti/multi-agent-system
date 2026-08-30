"""PostgreSQL checkpoint recovery across workflow runtime instances."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.model_gateway.deterministic import DeterministicBackend
from packages.schemas.cases import SupplierCase
from packages.schemas.reviews import (
    EvidenceConfirmation,
    EvidenceConfirmationDecision,
    ReviewExecutionStatus,
)


@pytest.mark.integration
async def test_postgres_checkpoint_survives_runtime_restart(
    risky_supplier_case: SupplierCase,
) -> None:
    database_url = os.getenv("TEST_LANGGRAPH_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_LANGGRAPH_DATABASE_URL is not configured")

    execution_id = uuid4()
    async with AsyncPostgresSaver.from_conn_string(database_url) as first_checkpointer:
        await first_checkpointer.setup()
        first_runtime = SupplierReviewWorkflow(DeterministicBackend(), first_checkpointer)
        paused = await first_runtime.start(
            risky_supplier_case,
            execution_id=execution_id,
            require_evidence_confirmation=True,
        )
        assert paused.status is ReviewExecutionStatus.AWAITING_INPUT

    async with AsyncPostgresSaver.from_conn_string(database_url) as second_checkpointer:
        second_runtime = SupplierReviewWorkflow(DeterministicBackend(), second_checkpointer)
        recovered = await second_runtime.get_execution(risky_supplier_case, execution_id)
        completed = await second_runtime.resume(
            risky_supplier_case,
            execution_id,
            EvidenceConfirmation(
                decision=EvidenceConfirmationDecision.CONFIRM,
                comment="Recovered after the original runtime connection was closed.",
            ),
        )

        assert recovered.status is ReviewExecutionStatus.AWAITING_INPUT
        assert completed.status is ReviewExecutionStatus.COMPLETED
        assert completed.result is not None
        assert completed.result.review_id == execution_id
        await second_checkpointer.adelete_thread(str(execution_id))
