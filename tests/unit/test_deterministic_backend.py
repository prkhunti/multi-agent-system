"""Tests for the offline deterministic model backend."""

from __future__ import annotations

from packages.model_gateway.base import ModelRequest, ModelTask
from packages.model_gateway.deterministic import DeterministicBackend
from packages.schemas.reviews import FindingBatch, RiskCategory


async def test_security_review_returns_schema_valid_findings() -> None:
    backend = DeterministicBackend()
    request = ModelRequest(
        task=ModelTask.SECURITY_REVIEW,
        system_prompt="Review security.",
        user_prompt="Review supplier.",
        context={
            "case_id": "case-1",
            "documents": [
                {
                    "title": "Questionnaire",
                    "content": "Shared credentials are permitted.",
                    "source_uri": None,
                }
            ],
        },
        schema_name="FindingBatch",
        response_schema=FindingBatch.model_json_schema(),
    )

    result = FindingBatch.model_validate_json(await backend.generate(request))

    assert {finding.category for finding in result.findings} == {RiskCategory.SECURITY}
    assert any(finding.evidence for finding in result.findings)
