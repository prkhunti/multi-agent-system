"""Shared test fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from packages.schemas.cases import CaseStatus, DocumentInput, SupplierCase


@pytest.fixture
def risky_supplier_case() -> SupplierCase:
    """Return a case containing findings in all three review domains."""
    now = datetime.now(UTC)
    return SupplierCase(
        id=uuid4(),
        tenant_id="tenant-test",
        supplier_name="Northstar Analytics",
        description="Critical analytics processor with access to internal data.",
        status=CaseStatus.NEW,
        documents=[
            DocumentInput(
                title="Security questionnaire",
                content="Administrators may use shared credentials during emergency support.",
                source_uri="s3://test/security-questionnaire.pdf",
            ),
            DocumentInput(
                title="Master services agreement",
                content="The customer accepts unlimited liability and automatic renewal.",
                source_uri="s3://test/msa.pdf",
            ),
            DocumentInput(
                title="Financial statement",
                content="The auditor identified a material going concern uncertainty.",
                source_uri="s3://test/financials.pdf",
            ),
        ],
        created_at=now,
        updated_at=now,
    )
