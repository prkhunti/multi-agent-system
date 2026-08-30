"""Read a governed action status for the production Step Functions workflow."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from packages.persistence.database import create_database_engine
from packages.persistence.models import GovernedActionRecord
from packages.persistence.secrets import resolve_database_settings
from packages.schemas.actions import (
    ActionStatus,
    ApprovalStatusRequest,
    ApprovalStatusResponse,
)
from packages.settings import Settings


async def _read_status(request: ApprovalStatusRequest) -> ApprovalStatusResponse:
    settings = resolve_database_settings(Settings())
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            status = await connection.scalar(
                select(GovernedActionRecord.status).where(
                    GovernedActionRecord.id == request.action_id
                )
            )
    finally:
        await engine.dispose()
    if not isinstance(status, str):
        raise RuntimeError(f"Governed action {request.action_id} was not found")
    return ApprovalStatusResponse(action_id=request.action_id, status=ActionStatus(status))


def handler(event: dict[str, Any], _: Any) -> dict[str, Any]:
    """Validate an action identifier and return its committed approval state."""
    request = ApprovalStatusRequest.model_validate(event)
    response = asyncio.run(_read_status(request))
    return response.model_dump(mode="json")
