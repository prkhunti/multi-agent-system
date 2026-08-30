"""Exercise the running Supplier Assurance API without external model calls."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from mcp import Client


def _request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = {
        "content-type": "application/json",
        "X-Actor-ID": "smoke-analyst@example.com",
        "X-Tenant-ID": "tenant-smoke",
        "X-Roles": "analyst",
    }
    request_headers.update(headers or {})
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed local smoke target
        return json.load(response)


async def _execute_approved_action(action_id: str) -> dict[str, Any]:
    mcp_url = os.getenv("MCP_BASE_URL", "http://localhost:8001/mcp")
    async with Client(mcp_url, raise_exceptions=True) as client:
        result = await client.call_tool(
            "execute_supplier_decision",
            {"action_id": action_id},
        )
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content


def _wait_for_workflow_reference(action_id: str, timeout_seconds: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        action = _request("GET", f"/api/v1/actions/{action_id}")
        if action["workflow_execution_arn"]:
            return action
        time.sleep(0.2)
    raise TimeoutError(f"Outbox delivery did not start workflow for action {action_id}")


def main() -> None:
    """Run one synthetic supplier case through review and governed execution."""
    health = _request("GET", "/health")
    assert health == {
        "status": "ok",
        "model_backend": "deterministic",
        "repository_backend": "postgres",
    }

    supplier_case = _request(
        "POST",
        "/api/v1/cases",
        {
            "supplier_name": "Northstar Smoke Test",
            "description": "Synthetic case used by the local end-to-end smoke test.",
            "documents": [
                {
                    "title": "Security questionnaire",
                    "content": "# Access control\nAdministrators may use shared credentials.",
                },
                {
                    "title": "Master services agreement",
                    "content": "# Liability\nThe customer accepts unlimited liability.",
                },
            ],
        },
    )
    case_id = supplier_case["id"]

    index_result = _request("POST", f"/api/v1/cases/{case_id}/documents/index")
    assert index_result["document_count"] == 2
    assert index_result["chunk_count"] >= 2

    query = quote("shared administrator credentials")
    evidence = _request("GET", f"/api/v1/cases/{case_id}/evidence/search?query={query}")
    assert evidence
    assert all(item["case_id"] == case_id for item in evidence)

    execution = _request(
        "POST",
        f"/api/v1/cases/{case_id}/review-executions",
        {
            "idempotency_key": str(uuid4()),
            "require_evidence_confirmation": True,
        },
    )
    assert execution["status"] == "awaiting_input"
    execution_id = execution["execution_id"]
    recovered = _request(
        "GET",
        f"/api/v1/cases/{case_id}/review-executions/{execution_id}",
    )
    assert recovered == execution
    execution = _request(
        "POST",
        f"/api/v1/cases/{case_id}/review-executions/{execution_id}/resume",
        {
            "decision": "confirm",
            "comment": "Smoke-test evidence confirmation.",
        },
    )
    assert execution["status"] == "completed"
    review = execution["result"]
    latest_review = _request("GET", f"/api/v1/cases/{case_id}/reviews/latest")
    assert latest_review["review_id"] == review["review_id"]
    assert latest_review["case_id"] == case_id
    assert latest_review["findings"]

    action = _request(
        "POST",
        f"/api/v1/cases/{case_id}/actions",
        {"idempotency_key": str(uuid4())},
    )
    assert action["status"] == "pending_approval"
    assert action["arguments"]["review_id"] == review["review_id"]
    action = _wait_for_workflow_reference(action["id"])
    assert action["workflow_execution_arn"].startswith("local://approval/")

    approved = _request(
        "POST",
        f"/api/v1/actions/{action['id']}/decision",
        {"decision": "approve", "comment": "Smoke-test approval"},
        headers={
            "X-Actor-ID": "smoke-approver@example.com",
            "X-Tenant-ID": "tenant-smoke",
            "X-Roles": "approver",
        },
    )
    assert approved["status"] == "approved"

    receipt = asyncio.run(_execute_approved_action(action["id"]))
    assert receipt["action_id"] == action["id"]
    assert receipt["applied_decision"] == approved["arguments"]["decision"]

    executed = _request("GET", f"/api/v1/actions/{action['id']}")
    assert executed["status"] == "executed"

    audit_events = _request("GET", f"/api/v1/cases/{case_id}/audit-events")
    event_types = {event["event_type"] for event in audit_events}
    assert {
        "case.created",
        "documents.indexed",
        "review.started",
        "review.completed",
        "action.proposed",
        "action.approved",
        "action.executed",
    }.issubset(event_types)

    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": case_id,
                "execution_id": execution_id,
                "review_id": review["review_id"],
                "action_id": action["id"],
                "action_status": executed["status"],
                "workflow_reference": action["workflow_execution_arn"],
                "external_reference": receipt["external_reference"],
                "chunks": index_result["chunk_count"],
                "evidence_hits": len(evidence),
                "audit_events": len(audit_events),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
