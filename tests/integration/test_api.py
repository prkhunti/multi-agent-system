"""HTTP boundary tests for the first vertical slice."""

from __future__ import annotations

from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from apps.api.dependencies import create_container
from apps.api.main import create_app
from packages.settings import Settings


async def test_create_and_review_case() -> None:
    settings = Settings(app_env="test", model_backend="deterministic", _env_file=None)
    app = create_app(create_container(settings))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "X-Actor-ID": "analyst@example.com",
            "X-Tenant-ID": "tenant-northstar",
            "X-Roles": "analyst",
        },
    ) as client:
        created = await client.post(
            "/api/v1/cases",
            json={
                "supplier_name": "Northstar Analytics",
                "description": "Critical data processor",
                "documents": [
                    {
                        "title": "Security questionnaire",
                        "content": "Shared credentials are permitted for support.",
                    },
                    {
                        "title": "Contract",
                        "content": "The contract contains unlimited liability.",
                    },
                ],
            },
        )
        assert created.status_code == 201
        case_id = created.json()["id"]
        assert created.json()["tenant_id"] == "tenant-northstar"

        reviewed = await client.post(f"/api/v1/cases/{case_id}/reviews")

        assert reviewed.status_code == 200
        payload = reviewed.json()
        assert payload["case_id"] == case_id
        assert payload["recommendation"]["decision"] == "escalate"
        assert payload["model_backend"] == "deterministic"

        latest = await client.get(f"/api/v1/cases/{case_id}/reviews/latest")
        assert latest.status_code == 200
        assert latest.json()["review_id"] == payload["review_id"]

        indexed = await client.post(f"/api/v1/cases/{case_id}/documents/index")
        assert indexed.status_code == 200
        assert indexed.json()["document_count"] == 2
        assert indexed.json()["chunk_count"] >= 2

        search = await client.get(
            f"/api/v1/cases/{case_id}/evidence/search",
            params={"query": "shared credentials"},
        )
        assert search.status_code == 200
        assert search.json()[0]["document_title"] == "Security questionnaire"

        fetched = await client.get(f"/api/v1/cases/{case_id}")
        assert fetched.json()["status"] == "pending_approval"

        proposed = await client.post(
            f"/api/v1/cases/{case_id}/actions",
            json={"idempotency_key": str(uuid4())},
        )
        assert proposed.status_code == 201
        action = proposed.json()
        assert action["status"] == "pending_approval"
        assert action["arguments"]["review_id"] == payload["review_id"]
        assert action["arguments"]["decision"] == "escalate"

        self_approval = await client.post(
            f"/api/v1/actions/{action['id']}/decision",
            headers={"X-Roles": "analyst,approver"},
            json={"decision": "approve", "comment": "Attempted self approval"},
        )
        assert self_approval.status_code == 403

        approved = await client.post(
            f"/api/v1/actions/{action['id']}/decision",
            headers={
                "X-Actor-ID": "approver@example.com",
                "X-Tenant-ID": "tenant-northstar",
                "X-Roles": "approver",
            },
            json={"decision": "approve", "comment": "Risk committee approved"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        hidden_from_other_tenant = await client.get(
            f"/api/v1/actions/{action['id']}",
            headers={
                "X-Actor-ID": "other@example.com",
                "X-Tenant-ID": "tenant-other",
                "X-Roles": "approver",
            },
        )
        assert hidden_from_other_tenant.status_code == 404

        audit = await client.get(f"/api/v1/cases/{case_id}/audit-events")
        event_types = {event["event_type"] for event in audit.json()}
        assert {
            "case.created",
            "review.started",
            "review.completed",
            "documents.indexed",
            "action.proposed",
            "action.approved",
        } <= event_types


async def test_business_endpoints_require_identity_headers() -> None:
    settings = Settings(app_env="test", model_backend="deterministic", _env_file=None)
    app = create_app(create_container(settings))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/cases",
            json={"supplier_name": "Unauthenticated Supplier"},
        )

    assert response.status_code == 401


async def test_durable_review_execution_pause_resume_and_tenant_scope() -> None:
    settings = Settings(app_env="test", model_backend="deterministic", _env_file=None)
    app = create_app(create_container(settings))
    analyst_headers = {
        "X-Actor-ID": "analyst@example.com",
        "X-Tenant-ID": "tenant-northstar",
        "X-Roles": "analyst",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=analyst_headers,
    ) as client:
        created = await client.post(
            "/api/v1/cases",
            json={
                "supplier_name": "Durable Northstar",
                "documents": [
                    {
                        "title": "Security questionnaire",
                        "content": "Shared credentials are permitted for support.",
                    },
                    {
                        "title": "Contract",
                        "content": "The contract contains unlimited liability.",
                    },
                ],
            },
        )
        case_id = created.json()["id"]
        start_payload = {
            "idempotency_key": str(uuid4()),
            "require_evidence_confirmation": True,
        }

        paused = await client.post(
            f"/api/v1/cases/{case_id}/review-executions",
            json=start_payload,
        )
        replayed = await client.post(
            f"/api/v1/cases/{case_id}/review-executions",
            json=start_payload,
        )

        assert paused.status_code == 201
        assert paused.json()["status"] == "awaiting_input"
        assert paused.json()["interrupt"]["type"] == "evidence_confirmation"
        assert replayed.json() == paused.json()
        execution_id = paused.json()["execution_id"]

        hidden = await client.get(
            f"/api/v1/cases/{case_id}/review-executions/{execution_id}",
            headers={
                "X-Actor-ID": "other@example.com",
                "X-Tenant-ID": "tenant-other",
                "X-Roles": "analyst",
            },
        )
        assert hidden.status_code == 404

        resumed = await client.post(
            f"/api/v1/cases/{case_id}/review-executions/{execution_id}/resume",
            json={
                "decision": "confirm",
                "comment": "Evidence checked against the source documents.",
            },
        )
        repeated_resume = await client.post(
            f"/api/v1/cases/{case_id}/review-executions/{execution_id}/resume",
            json={"decision": "confirm", "comment": "Repeated client delivery."},
        )

        assert resumed.status_code == 200
        assert resumed.json()["status"] == "completed"
        assert resumed.json()["result"]["review_id"] == execution_id
        assert repeated_resume.json() == resumed.json()

        latest = await client.get(f"/api/v1/cases/{case_id}/reviews/latest")
        assert latest.json()["review_id"] == execution_id
        fetched = await client.get(f"/api/v1/cases/{case_id}")
        assert fetched.json()["status"] == "pending_approval"

        audit = await client.get(f"/api/v1/cases/{case_id}/audit-events")
        event_types = [event["event_type"] for event in audit.json()]
        assert event_types.count("review.started") == 1
        assert event_types.count("review.awaiting_input") == 1
        assert event_types.count("review.resumed") == 1
        assert event_types.count("review.completed") == 1
