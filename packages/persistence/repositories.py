"""Case repository contracts and implementations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from packages.persistence.models import (
    AuditEventRecord,
    DocumentRecord,
    FindingRecord,
    ReviewRunRecord,
    SupplierCaseRecord,
)
from packages.schemas.audit import AuditEvent, AuditEventCreate
from packages.schemas.cases import CaseStatus, DocumentInput, SupplierCase
from packages.schemas.reviews import (
    Evidence,
    Finding,
    Recommendation,
    ReviewResult,
    RiskCategory,
    Severity,
)


class CaseNotFoundError(LookupError):
    """Raised when a supplier case does not exist."""


class ReviewNotFoundError(LookupError):
    """Raised when a supplier case has no completed review."""


class CaseRepository(Protocol):
    """Persistence operations required by the API."""

    async def create(self, supplier_case: SupplierCase) -> SupplierCase:
        """Persist a new supplier case."""
        ...

    async def get(self, case_id: UUID) -> SupplierCase:
        """Return a supplier case."""
        ...

    async def set_status(self, case_id: UUID, status: CaseStatus) -> SupplierCase:
        """Update the case lifecycle status."""
        ...

    async def save_review(self, result: ReviewResult) -> ReviewResult:
        """Persist a completed review and its findings."""
        ...

    async def get_latest_review(self, case_id: UUID) -> ReviewResult:
        """Return the most recent completed review."""
        ...

    async def get_review(self, review_id: UUID) -> ReviewResult:
        """Return one completed review by identifier."""
        ...

    async def append_audit_event(self, event: AuditEventCreate) -> AuditEvent:
        """Append an immutable audit event."""
        ...

    async def list_audit_events(self, case_id: UUID) -> list[AuditEvent]:
        """Return audit events in creation order."""
        ...


class InMemoryCaseRepository:
    """Concurrency-safe repository used by tests and offline development."""

    def __init__(self) -> None:
        self._cases: dict[UUID, SupplierCase] = {}
        self._reviews: dict[UUID, list[ReviewResult]] = {}
        self._reviews_by_id: dict[UUID, ReviewResult] = {}
        self._events: dict[UUID, list[AuditEvent]] = {}
        self._lock = asyncio.Lock()

    async def create(self, supplier_case: SupplierCase) -> SupplierCase:
        """Persist a new supplier case."""
        async with self._lock:
            self._cases[supplier_case.id] = supplier_case
        return supplier_case

    async def get(self, case_id: UUID) -> SupplierCase:
        """Return a case or raise a domain-specific error."""
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise CaseNotFoundError(f"Supplier case {case_id} was not found") from exc

    async def set_status(self, case_id: UUID, status: CaseStatus) -> SupplierCase:
        """Update case lifecycle status."""
        async with self._lock:
            current = await self.get(case_id)
            updated = current.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
            self._cases[case_id] = updated
            return updated

    async def save_review(self, result: ReviewResult) -> ReviewResult:
        """Persist a completed review idempotently."""
        async with self._lock:
            existing = self._reviews_by_id.get(result.review_id)
            if existing is not None:
                return existing
            self._reviews.setdefault(result.case_id, []).append(result)
            self._reviews_by_id[result.review_id] = result
        return result

    async def get_latest_review(self, case_id: UUID) -> ReviewResult:
        """Return the most recent completed review."""
        await self.get(case_id)
        reviews = self._reviews.get(case_id, [])
        if not reviews:
            raise ReviewNotFoundError(f"Supplier case {case_id} has no completed review")
        return reviews[-1]

    async def get_review(self, review_id: UUID) -> ReviewResult:
        """Return one completed review by identifier."""
        try:
            return self._reviews_by_id[review_id]
        except KeyError as exc:
            raise ReviewNotFoundError(f"Review {review_id} was not found") from exc

    async def append_audit_event(self, event: AuditEventCreate) -> AuditEvent:
        """Append an immutable audit event."""
        await self.get(event.case_id)
        stored = AuditEvent(
            **event.model_dump(),
            id=uuid4(),
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._events.setdefault(event.case_id, []).append(stored)
        return stored

    async def list_audit_events(self, case_id: UUID) -> list[AuditEvent]:
        """Return audit events in creation order."""
        await self.get(case_id)
        return list(self._events.get(case_id, []))


class SqlAlchemyCaseRepository:
    """Durable case repository backed by async SQLAlchemy sessions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, supplier_case: SupplierCase) -> SupplierCase:
        """Persist a new supplier case and its supplied documents."""
        record = SupplierCaseRecord(
            id=supplier_case.id,
            tenant_id=supplier_case.tenant_id,
            supplier_name=supplier_case.supplier_name,
            description=supplier_case.description,
            status=supplier_case.status.value,
            created_at=supplier_case.created_at,
            updated_at=supplier_case.updated_at,
            documents=[
                DocumentRecord(
                    title=document.title,
                    position=position,
                    content=document.content,
                    source_uri=document.source_uri,
                    metadata_json={},
                )
                for position, document in enumerate(supplier_case.documents)
            ],
        )
        async with self._sessions() as session:
            session.add(record)
            await session.commit()
        return supplier_case

    async def get(self, case_id: UUID) -> SupplierCase:
        """Return a supplier case with documents eagerly loaded."""
        statement = (
            select(SupplierCaseRecord)
            .options(selectinload(SupplierCaseRecord.documents))
            .where(SupplierCaseRecord.id == case_id)
        )
        async with self._sessions() as session:
            record = (await session.scalars(statement)).one_or_none()
        if record is None:
            raise CaseNotFoundError(f"Supplier case {case_id} was not found")
        return self._case_from_record(record)

    async def set_status(self, case_id: UUID, status: CaseStatus) -> SupplierCase:
        """Update case lifecycle status."""
        async with self._sessions() as session:
            record = await session.get(SupplierCaseRecord, case_id)
            if record is None:
                raise CaseNotFoundError(f"Supplier case {case_id} was not found")
            record.status = status.value
            record.updated_at = datetime.now(UTC)
            await session.commit()
        return await self.get(case_id)

    async def save_review(self, result: ReviewResult) -> ReviewResult:
        """Persist a completed review and all findings atomically and idempotently."""
        review = ReviewRunRecord(
            id=result.review_id,
            case_id=result.case_id,
            model_backend=result.model_backend,
            decision=result.recommendation.decision.value,
            rationale=result.recommendation.rationale,
            required_actions_json=result.recommendation.required_actions,
            confidence=result.recommendation.confidence,
            started_at=result.started_at,
            completed_at=result.completed_at,
        )
        findings = [
            FindingRecord(
                id=finding.id,
                review_run_id=result.review_id,
                category=finding.category.value,
                severity=finding.severity.value,
                title=finding.title,
                summary=finding.summary,
                evidence_json=[item.model_dump(mode="json") for item in finding.evidence],
                remediation=finding.remediation,
                confidence=finding.confidence,
            )
            for finding in result.findings
        ]
        async with self._sessions() as session:
            existing = await session.get(ReviewRunRecord, result.review_id)
            if existing is not None:
                return await self.get_review(result.review_id)
            session.add(review)
            # Flush the parent first because findings are constructed with a foreign-key
            # identifier rather than an ORM relationship. PostgreSQL enforces this ordering.
            await session.flush()
            session.add_all(findings)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                try:
                    return await self.get_review(result.review_id)
                except ReviewNotFoundError:
                    raise
        return result

    async def get_latest_review(self, case_id: UUID) -> ReviewResult:
        """Return the most recent completed review."""
        await self.get(case_id)
        statement = (
            select(ReviewRunRecord)
            .where(ReviewRunRecord.case_id == case_id)
            .order_by(ReviewRunRecord.completed_at.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            review = (await session.scalars(statement)).one_or_none()
        if review is None:
            raise ReviewNotFoundError(f"Supplier case {case_id} has no completed review")
        return await self.get_review(review.id)

    async def get_review(self, review_id: UUID) -> ReviewResult:
        """Return one completed review by identifier."""
        async with self._sessions() as session:
            review = await session.get(ReviewRunRecord, review_id)
            if review is None:
                raise ReviewNotFoundError(f"Review {review_id} was not found")
            finding_statement = select(FindingRecord).where(
                FindingRecord.review_run_id == review.id
            )
            findings = list((await session.scalars(finding_statement)).all())
        return self._review_from_records(review, findings)

    async def append_audit_event(self, event: AuditEventCreate) -> AuditEvent:
        """Append an immutable audit event."""
        await self.get(event.case_id)
        stored = AuditEvent(
            **event.model_dump(),
            id=uuid4(),
            created_at=datetime.now(UTC),
        )
        async with self._sessions() as session:
            session.add(
                AuditEventRecord(
                    id=stored.id,
                    case_id=stored.case_id,
                    event_type=stored.event_type,
                    actor_id=stored.actor_id,
                    payload_json=stored.payload,
                    created_at=stored.created_at,
                )
            )
            await session.commit()
        return stored

    async def list_audit_events(self, case_id: UUID) -> list[AuditEvent]:
        """Return audit events in creation order."""
        await self.get(case_id)
        statement = (
            select(AuditEventRecord)
            .where(AuditEventRecord.case_id == case_id)
            .order_by(AuditEventRecord.created_at, AuditEventRecord.id)
        )
        async with self._sessions() as session:
            records = list((await session.scalars(statement)).all())
        return [
            AuditEvent(
                id=record.id,
                case_id=record.case_id,
                event_type=record.event_type,
                actor_id=record.actor_id,
                payload=record.payload_json,
                created_at=record.created_at,
            )
            for record in records
        ]

    def _case_from_record(self, record: SupplierCaseRecord) -> SupplierCase:
        documents = sorted(record.documents, key=lambda item: item.position)
        return SupplierCase(
            id=record.id,
            tenant_id=record.tenant_id,
            supplier_name=record.supplier_name,
            description=record.description,
            status=CaseStatus(record.status),
            documents=[
                DocumentInput(
                    title=document.title,
                    content=document.content,
                    source_uri=document.source_uri,
                )
                for document in documents
            ],
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _review_from_records(
        self,
        review: ReviewRunRecord,
        findings: list[FindingRecord],
    ) -> ReviewResult:
        if review.decision is None or review.completed_at is None or review.confidence is None:
            raise ValueError(f"Review {review.id} is incomplete")
        return ReviewResult(
            review_id=review.id,
            case_id=review.case_id,
            model_backend=review.model_backend,
            findings=[
                Finding(
                    id=finding.id,
                    category=RiskCategory(finding.category),
                    severity=Severity(finding.severity),
                    title=finding.title,
                    summary=finding.summary,
                    evidence=[Evidence.model_validate(item) for item in finding.evidence_json],
                    remediation=finding.remediation,
                    confidence=finding.confidence,
                )
                for finding in findings
            ],
            recommendation=Recommendation(
                decision=review.decision,
                rationale=review.rationale or "",
                required_actions=review.required_actions_json,
                confidence=review.confidence,
            ),
            started_at=review.started_at,
            completed_at=review.completed_at,
        )
