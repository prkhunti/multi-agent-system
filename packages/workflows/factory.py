"""Approval workflow adapter construction."""

from __future__ import annotations

import boto3

from packages.settings import Settings
from packages.workflows.approvals import (
    ApprovalWorkflow,
    LocalApprovalWorkflow,
    StepFunctionsApprovalWorkflow,
)


def create_approval_workflow(settings: Settings) -> ApprovalWorkflow:
    """Create the configured approval workflow adapter.

    Parameters
    ----------
    settings : Settings
        Validated application configuration.

    Returns
    -------
    ApprovalWorkflow
        Local deterministic or AWS Step Functions adapter.
    """
    if settings.approval_workflow_backend == "step_functions":
        client = boto3.client("stepfunctions", region_name=settings.aws_region)
        return StepFunctionsApprovalWorkflow(client, settings.step_functions_state_machine_arn)
    return LocalApprovalWorkflow()
