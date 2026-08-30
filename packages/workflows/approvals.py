"""Local and AWS Step Functions approval workflow adapters."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from botocore.exceptions import ClientError

from packages.schemas.actions import GovernedAction


class ApprovalWorkflow(Protocol):
    """Outer business workflow operations used by action governance."""

    async def start(self, action: GovernedAction) -> str:
        """Start an approval workflow and return its durable reference."""
        ...


class LocalApprovalWorkflow:
    """Credential-free approval workflow used in local development."""

    async def start(self, action: GovernedAction) -> str:
        """Return a stable local workflow reference."""
        return f"local://approval/{action.id}"


class _StepFunctionsClient(Protocol):
    def start_execution(self, **kwargs: Any) -> dict[str, Any]:
        """Start a state machine execution."""
        ...

    def send_task_success(self, **kwargs: Any) -> dict[str, Any]:
        """Complete a callback task."""
        ...


class StepFunctionsApprovalWorkflow:
    """AWS Step Functions Standard Workflow adapter for human approval."""

    def __init__(self, client: _StepFunctionsClient, state_machine_arn: str) -> None:
        if not state_machine_arn:
            raise ValueError("A Step Functions state machine ARN is required")
        self._client = client
        self._state_machine_arn = state_machine_arn

    async def start(self, action: GovernedAction) -> str:
        """Start an idempotently named approval execution."""
        execution_name = f"supplier-action-{action.id.hex}"
        try:
            response = await asyncio.to_thread(
                self._client.start_execution,
                stateMachineArn=self._state_machine_arn,
                name=execution_name,
                input=json.dumps(
                    {
                        "action_id": str(action.id),
                        "case_id": str(action.case_id),
                        "tenant_id": action.tenant_id,
                        "tool_name": action.tool_name,
                        "status": action.status.value,
                    }
                ),
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ExecutionAlreadyExists":
                raise
            return self._execution_arn(execution_name)
        execution_arn = response.get("executionArn")
        if not isinstance(execution_arn, str):
            raise RuntimeError("Step Functions did not return an execution ARN")
        return execution_arn

    def _execution_arn(self, execution_name: str) -> str:
        marker = ":stateMachine:"
        if marker not in self._state_machine_arn:
            raise ValueError("The Step Functions state machine ARN is invalid")
        prefix, state_machine_name = self._state_machine_arn.split(marker, maxsplit=1)
        if ":" in state_machine_name:
            raise ValueError("Use an unqualified state machine ARN for idempotent delivery")
        return f"{prefix}:execution:{state_machine_name}:{execution_name}"

    async def complete_callback(
        self,
        *,
        task_token: str,
        action: GovernedAction,
    ) -> None:
        """Return an approval decision to a waiting callback task."""
        await asyncio.to_thread(
            self._client.send_task_success,
            taskToken=task_token,
            output=json.dumps(
                {
                    "action_id": str(action.id),
                    "status": action.status.value,
                    "approver_id": action.approver_id,
                }
            ),
        )
