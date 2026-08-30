"""MCP v2 server for approval-gated enterprise supplier actions."""

from __future__ import annotations

from uuid import UUID

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from packages.governance.repositories import SqlAlchemyActionRepository
from packages.governance.service import GovernanceService, InMemorySupplierSystem
from packages.persistence.database import create_database_engine, create_session_factory
from packages.schemas.actions import ExecutionReceipt
from packages.schemas.identity import Principal, Role
from packages.settings import Settings, get_settings
from packages.workflows.approvals import LocalApprovalWorkflow


def create_mcp_server(governance: GovernanceService) -> MCPServer:
    """Create the closed-world MCP execution server.

    Parameters
    ----------
    governance : GovernanceService
        Policy service that re-loads and validates every action before execution.

    Returns
    -------
    MCPServer
        MCP server exposing only the approved-action execution capability.
    """
    server = MCPServer(
        "Supplier Assurance Enterprise Tools",
        instructions=(
            "Execute only pre-approved supplier actions by identifier. "
            "Never infer or replace action arguments."
        ),
    )
    service_principal = Principal(
        subject="service:mcp-supplier-executor",
        tenant_id="service",
        roles=frozenset({Role.ADMIN, Role.EXECUTOR}),
    )

    @server.tool(
        name="execute_supplier_decision",
        title="Execute approved supplier decision",
        description=(
            "Execute the immutable arguments of a previously approved supplier action. "
            "Pending, rejected, and unknown actions are refused."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def execute_supplier_decision(action_id: UUID) -> ExecutionReceipt:
        """Execute a persisted and human-approved supplier decision."""
        return await governance.execute(action_id, service_principal)

    return server


def _create_runtime_server(settings: Settings) -> MCPServer:
    if settings.repository_backend != "postgres":
        raise RuntimeError("The deployed MCP server requires REPOSITORY_BACKEND=postgres")
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyActionRepository(sessions)
    governance = GovernanceService(
        repository=repository,
        approval_workflow=LocalApprovalWorkflow(),
        supplier_system=InMemorySupplierSystem(),
    )
    return create_mcp_server(governance)


def main() -> None:
    """Run the MCP server over stateless Streamable HTTP."""
    settings = get_settings()
    server = _create_runtime_server(settings)
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
