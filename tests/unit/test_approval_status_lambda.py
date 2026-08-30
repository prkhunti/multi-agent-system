"""Tests for the private Step Functions approval-status Lambda."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.approval_status import lambda_handler
from packages.schemas.actions import (
    ActionStatus,
    ApprovalStatusRequest,
    ApprovalStatusResponse,
)


def test_handler_validates_and_serializes_committed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_id = uuid4()

    async def fake_read(request: ApprovalStatusRequest) -> ApprovalStatusResponse:
        assert request.action_id == action_id
        return ApprovalStatusResponse(action_id=action_id, status=ActionStatus.APPROVED)

    monkeypatch.setattr(lambda_handler, "_read_status", fake_read)

    result = lambda_handler.handler({"action_id": str(action_id)}, None)

    assert result == {"action_id": str(action_id), "status": "approved"}


def test_handler_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        lambda_handler.handler(
            {"action_id": str(uuid4()), "task_token": "must-not-cross-this-boundary"},
            None,
        )
