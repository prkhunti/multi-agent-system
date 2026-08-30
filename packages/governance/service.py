"""Policy-enforced proposal, approval, and execution service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from packages.governance.repositories import (
    ActionRepository,
    ActionTransitionConflictError,
)
from packages.schemas.actions import (
    ActionProposalRequest,
    ActionStatus,
    ApprovalDecision,
    ApprovalRequest,
    ExecutionReceipt,
    GovernedAction,
    SupplierDecisionArguments,
)
from packages.schemas.audit import AuditEventCreate
from packages.schemas.cases import SupplierCase
from packages.schemas.identity import Principal, Role
from packages.schemas.outbox import (
    ApprovalWorkflowStartPayload,
    OutboxEventType,
    OutboxMessageCreate,
)
from packages.schemas.reviews import ReviewResult
from packages.workflows.approvals import ApprovalWorkflow


class AuthorizationDeniedError(PermissionError):
    """Raised when identity or separation-of-duties policy denies an operation."""


class ActionStateError(RuntimeError):
    """Raised when an action cannot transition from its current state."""


class IdempotencyConflictError(RuntimeError):
    """Raised when one idempotency key is reused for different business input."""


class SupplierSystem(Protocol):
    """Narrow enterprise-system mutation boundary."""

    async def set_onboarding_decision(
        self,
        *,
        action_id: UUID,
        idempotency_key: UUID,
        arguments: SupplierDecisionArguments,
    ) -> ExecutionReceipt:
        """Apply an approved supplier decision idempotently."""
        ...


class InMemorySupplierSystem:
    """Deterministic idempotent enterprise-system adapter for demonstrations."""

    def __init__(self) -> None:
        self._receipts: dict[UUID, ExecutionReceipt] = {}

    async def set_onboarding_decision(
        self,
        *,
        action_id: UUID,
        idempotency_key: UUID,
        arguments: SupplierDecisionArguments,
    ) -> ExecutionReceipt:
        """Apply an approved supplier decision once per idempotency key."""
        existing = self._receipts.get(idempotency_key)
        if existing is not None:
            return existing
        receipt = ExecutionReceipt(
            action_id=action_id,
            external_reference=f"supplier-system://decisions/{action_id}",
            applied_decision=arguments.decision,
            executed_at=datetime.now(UTC),
        )
        self._receipts[idempotency_key] = receipt
        return receipt


class GovernanceService:
    """Enforce authorization and immutable approval boundaries for tool actions."""

    def __init__(
        self,
        repository: ActionRepository,
        approval_workflow: ApprovalWorkflow,
        supplier_system: SupplierSystem,
    ) -> None:
        self._repository = repository
        self._approval_workflow = approval_workflow
        self._supplier_system = supplier_system

    async def propose(
        self,
        *,
        supplier_case: SupplierCase,
        review: ReviewResult,
        request: ActionProposalRequest,
        principal: Principal,
    ) -> GovernedAction:
        """Derive an immutable action from a completed review."""
        self._require_role(principal, Role.ANALYST)
        self._require_tenant(principal, supplier_case.tenant_id)
        if review.case_id != supplier_case.id:
            raise ValueError("The review does not belong to the supplier case")
        now = datetime.now(UTC)
        action = GovernedAction(
            id=uuid5(NAMESPACE_URL, f"{principal.tenant_id}:{request.idempotency_key}"),
            case_id=supplier_case.id,
            review_id=review.review_id,
            tenant_id=supplier_case.tenant_id,
            arguments=SupplierDecisionArguments(
                supplier_case_id=supplier_case.id,
                review_id=review.review_id,
                decision=review.recommendation.decision,
                rationale=review.recommendation.rationale,
                required_actions=review.recommendation.required_actions,
            ),
            status=ActionStatus.PENDING_APPROVAL,
            proposer_id=principal.subject,
            idempotency_key=request.idempotency_key,
            created_at=now,
        )
        if not self._repository.uses_transactional_outbox:
            workflow_reference = await self._approval_workflow.start(action)
            action = action.model_copy(update={"workflow_execution_arn": workflow_reference})
        audit_event = AuditEventCreate(
            case_id=action.case_id,
            event_type="action.proposed",
            actor_id=principal.subject,
            payload={
                "action_id": str(action.id),
                "review_id": str(action.review_id),
                "tool_name": action.tool_name,
            },
        )
        outbox_message = OutboxMessageCreate(
            aggregate_id=action.id,
            event_type=OutboxEventType.APPROVAL_WORKFLOW_START,
            payload=ApprovalWorkflowStartPayload(
                action_id=action.id,
                case_id=action.case_id,
                tenant_id=action.tenant_id,
            ),
        )
        stored, _created = await self._repository.create_proposal(
            action,
            audit_event,
            outbox_message,
        )
        if stored.case_id != action.case_id or stored.review_id != action.review_id:
            raise IdempotencyConflictError(
                "The idempotency key is already associated with another proposal"
            )
        return stored

    async def decide(
        self,
        *,
        action_id: UUID,
        request: ApprovalRequest,
        principal: Principal,
    ) -> GovernedAction:
        """Apply a human approval decision with separation of duties."""
        self._require_role(principal, Role.APPROVER)
        action = await self._repository.get(action_id)
        self._require_tenant(principal, action.tenant_id)
        if action.proposer_id == principal.subject:
            raise AuthorizationDeniedError("A proposer cannot approve their own action")
        if action.status is not ActionStatus.PENDING_APPROVAL:
            raise ActionStateError(f"Action {action.id} is already {action.status.value}")
        now = datetime.now(UTC)
        status = (
            ActionStatus.APPROVED
            if request.decision is ApprovalDecision.APPROVE
            else ActionStatus.REJECTED
        )
        decided = action.model_copy(
            update={
                "status": status,
                "approver_id": principal.subject,
                "approval_comment": request.comment,
                "decided_at": now,
            }
        )
        try:
            await self._repository.save_transition(
                decided,
                AuditEventCreate(
                    case_id=decided.case_id,
                    event_type=f"action.{decided.status.value}",
                    actor_id=principal.subject,
                    payload={
                        "action_id": str(decided.id),
                        "comment": decided.approval_comment,
                    },
                ),
                expected_status=ActionStatus.PENDING_APPROVAL,
            )
        except ActionTransitionConflictError as exc:
            raise ActionStateError(str(exc)) from exc
        return decided

    async def get(self, action_id: UUID, principal: Principal) -> GovernedAction:
        """Return an action within the principal's tenant boundary."""
        action = await self._repository.get(action_id)
        self._require_tenant(principal, action.tenant_id)
        return action

    async def execute(self, action_id: UUID, principal: Principal) -> ExecutionReceipt:
        """Execute only the immutable arguments of a previously approved action."""
        self._require_role(principal, Role.EXECUTOR)
        action = await self._repository.get(action_id)
        self._require_tenant(principal, action.tenant_id)
        if action.status is ActionStatus.EXECUTED and action.execution_receipt is not None:
            return action.execution_receipt
        if action.status is not ActionStatus.APPROVED:
            raise ActionStateError(f"Action {action.id} is not approved")
        receipt = await self._supplier_system.set_onboarding_decision(
            action_id=action.id,
            idempotency_key=action.idempotency_key,
            arguments=action.arguments,
        )
        executed = action.model_copy(
            update={
                "status": ActionStatus.EXECUTED,
                "execution_receipt": receipt,
                "executed_at": receipt.executed_at,
            }
        )
        try:
            await self._repository.save_transition(
                executed,
                AuditEventCreate(
                    case_id=executed.case_id,
                    event_type="action.executed",
                    actor_id=principal.subject,
                    payload={
                        "action_id": str(executed.id),
                        "external_reference": receipt.external_reference,
                    },
                ),
                expected_status=ActionStatus.APPROVED,
            )
        except ActionTransitionConflictError as exc:
            current = await self._repository.get(action_id)
            if current.status is ActionStatus.EXECUTED and current.execution_receipt is not None:
                return current.execution_receipt
            raise ActionStateError(str(exc)) from exc
        return receipt

    def _require_role(self, principal: Principal, role: Role) -> None:
        if not principal.has_role(role):
            raise AuthorizationDeniedError(f"The {role.value} role is required")

    def _require_tenant(self, principal: Principal, tenant_id: str) -> None:
        if Role.ADMIN not in principal.roles and principal.tenant_id != tenant_id:
            raise AuthorizationDeniedError("The resource belongs to another tenant")
