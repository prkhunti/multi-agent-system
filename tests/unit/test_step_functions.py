"""Tests for the AWS Step Functions approval adapter."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError

from packages.governance.repositories import InMemoryActionRepository
from packages.governance.service import GovernanceService, InMemorySupplierSystem
from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.model_gateway.deterministic import DeterministicBackend
from packages.schemas.actions import ActionProposalRequest, ApprovalDecision, ApprovalRequest
from packages.schemas.cases import SupplierCase
from packages.schemas.identity import Principal, Role
from packages.workflows.approvals import StepFunctionsApprovalWorkflow


class _FakeStepFunctionsClient:
    def __init__(self) -> None:
        self.start_kwargs: dict[str, Any] = {}
        self.success_kwargs: dict[str, Any] = {}

    def start_execution(self, **kwargs: Any) -> dict[str, Any]:
        if self.start_kwargs:
            raise ClientError(
                {"Error": {"Code": "ExecutionAlreadyExists", "Message": "duplicate"}},
                "StartExecution",
            )
        self.start_kwargs = kwargs
        state_machine_arn = kwargs["stateMachineArn"]
        prefix, state_machine_name = state_machine_arn.split(":stateMachine:", maxsplit=1)
        return {"executionArn": f"{prefix}:execution:{state_machine_name}:{kwargs['name']}"}

    def send_task_success(self, **kwargs: Any) -> dict[str, Any]:
        self.success_kwargs = kwargs
        return {}


async def test_step_functions_adapter_uses_stable_execution_and_callback_payload(
    risky_supplier_case: SupplierCase,
) -> None:
    client = _FakeStepFunctionsClient()
    workflow = StepFunctionsApprovalWorkflow(
        client,
        "arn:aws:states:us-east-1:123:stateMachine:supplier-approval",
    )
    service = GovernanceService(
        repository=InMemoryActionRepository(),
        approval_workflow=workflow,
        supplier_system=InMemorySupplierSystem(),
    )
    review = await SupplierReviewWorkflow(DeterministicBackend()).run(risky_supplier_case)
    action = await service.propose(
        supplier_case=risky_supplier_case,
        review=review,
        request=ActionProposalRequest(idempotency_key=uuid4()),
        principal=Principal(
            subject="analyst@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.ANALYST}),
        ),
    )
    action = await service.decide(
        action_id=action.id,
        request=ApprovalRequest(decision=ApprovalDecision.APPROVE),
        principal=Principal(
            subject="approver@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.APPROVER}),
        ),
    )
    replayed_reference = await workflow.start(action)
    await workflow.complete_callback(task_token="opaque-task-token", action=action)

    assert client.start_kwargs["name"] == f"supplier-action-{action.id.hex}"
    assert json.loads(client.start_kwargs["input"])["action_id"] == str(action.id)
    assert replayed_reference == action.workflow_execution_arn
    assert client.success_kwargs["taskToken"] == "opaque-task-token"
    assert json.loads(client.success_kwargs["output"])["status"] == "approved"
