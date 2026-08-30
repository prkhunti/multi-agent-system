"""In-process MCP protocol test for the governed execution tool."""

from __future__ import annotations

from uuid import uuid4

from mcp import Client

from apps.mcp_server.main import create_mcp_server
from packages.governance.repositories import InMemoryActionRepository
from packages.governance.service import GovernanceService, InMemorySupplierSystem
from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.model_gateway.deterministic import DeterministicBackend
from packages.schemas.actions import ActionProposalRequest, ApprovalDecision, ApprovalRequest
from packages.schemas.cases import SupplierCase
from packages.schemas.identity import Principal, Role
from packages.workflows.approvals import LocalApprovalWorkflow


async def test_mcp_server_executes_only_persisted_approved_action(
    risky_supplier_case: SupplierCase,
) -> None:
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
        principal=Principal(
            subject="analyst@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.ANALYST}),
        ),
    )
    approved = await service.decide(
        action_id=proposed.id,
        request=ApprovalRequest(decision=ApprovalDecision.APPROVE),
        principal=Principal(
            subject="approver@example.com",
            tenant_id="tenant-test",
            roles=frozenset({Role.APPROVER}),
        ),
    )
    server = create_mcp_server(service)

    async with Client(server, raise_exceptions=True) as client:
        listing = await client.list_tools()
        assert [tool.name for tool in listing.tools] == ["execute_supplier_decision"]
        tool = listing.tools[0]
        assert tool.annotations is not None
        assert tool.annotations.idempotent_hint is True

        result = await client.call_tool(
            "execute_supplier_decision",
            {"action_id": str(approved.id)},
        )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["action_id"] == str(approved.id)
    assert result.structured_content["applied_decision"] == approved.arguments.decision.value
