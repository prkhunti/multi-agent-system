"""FastAPI entrypoint for the supplier assurance service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status

from apps.api.dependencies import ApplicationContainer, create_container
from packages.governance.repositories import ActionNotFoundError
from packages.governance.service import (
    ActionStateError,
    AuthorizationDeniedError,
    IdempotencyConflictError,
)
from packages.graphs.supplier_review import (
    ReviewExecutionNotFoundError,
    ReviewExecutionStateError,
)
from packages.identity.auth import AuthenticationError
from packages.persistence.repositories import CaseNotFoundError, ReviewNotFoundError
from packages.schemas.actions import (
    ActionProposalRequest,
    ApprovalRequest,
    GovernedAction,
)
from packages.schemas.audit import AuditEvent, AuditEventCreate
from packages.schemas.cases import CaseStatus, SupplierCase, SupplierCaseCreate
from packages.schemas.documents import IndexResult, RetrievedChunk
from packages.schemas.identity import Principal, Role
from packages.schemas.reviews import (
    EvidenceConfirmation,
    ReviewExecution,
    ReviewExecutionStart,
    ReviewExecutionStatus,
    ReviewResult,
)


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    """Build the API application."""
    active_container = container or create_container()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await active_container.start()
        try:
            yield
        finally:
            await active_container.close()

    app = FastAPI(
        title="Supplier Assurance Copilot",
        version="0.1.0",
        description="Auditable multi-agent supplier onboarding and risk review.",
        lifespan=lifespan,
    )
    app.state.container = active_container

    def get_container(request: Request) -> ApplicationContainer:
        return request.app.state.container

    async def get_principal(
        request: Request,
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> Principal:
        try:
            return await dependencies.authenticator.authenticate(request.headers)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    async def get_authorized_case(
        case_id: UUID,
        principal: Principal,
        dependencies: ApplicationContainer,
    ) -> SupplierCase:
        try:
            supplier_case = await dependencies.cases.get(case_id)
        except CaseNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if Role.ADMIN not in principal.roles and supplier_case.tenant_id != principal.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier case {case_id} was not found",
            )
        return supplier_case

    def require_analyst(principal: Principal) -> None:
        if not principal.has_role(Role.ANALYST):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The analyst role is required",
            )

    async def persist_completed_review(
        result: ReviewResult,
        dependencies: ApplicationContainer,
    ) -> bool:
        try:
            await dependencies.cases.get_review(result.review_id)
            return False
        except ReviewNotFoundError:
            pass

        await dependencies.cases.save_review(result)
        await dependencies.cases.set_status(result.case_id, CaseStatus.PENDING_APPROVAL)
        await dependencies.cases.append_audit_event(
            AuditEventCreate(
                case_id=result.case_id,
                event_type="review.completed",
                actor_id="system:review-workflow",
                payload={
                    "review_id": str(result.review_id),
                    "decision": result.recommendation.decision.value,
                    "finding_count": len(result.findings),
                },
            )
        )
        return True

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        """Return process health and active model backend."""
        settings = app.state.container.settings
        return {
            "status": "ok",
            "model_backend": settings.model_backend,
            "repository_backend": settings.repository_backend,
        }

    @app.post(
        "/api/v1/cases",
        response_model=SupplierCase,
        status_code=status.HTTP_201_CREATED,
        tags=["cases"],
    )
    async def create_case(
        payload: SupplierCaseCreate,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> SupplierCase:
        """Create a supplier onboarding case."""
        now = datetime.now(UTC)
        supplier_case = SupplierCase(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            supplier_name=payload.supplier_name,
            description=payload.description,
            status=CaseStatus.NEW,
            documents=payload.documents,
            created_at=now,
            updated_at=now,
        )
        created = await dependencies.cases.create(supplier_case)
        await dependencies.cases.append_audit_event(
            AuditEventCreate(
                case_id=created.id,
                event_type="case.created",
                actor_id=principal.subject,
                payload={"supplier_name": created.supplier_name},
            )
        )
        return created

    @app.get(
        "/api/v1/cases/{case_id}",
        response_model=SupplierCase,
        tags=["cases"],
    )
    async def get_case(
        case_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> SupplierCase:
        """Return a supplier case."""
        return await get_authorized_case(case_id, principal, dependencies)

    @app.post(
        "/api/v1/cases/{case_id}/reviews",
        response_model=ReviewResult,
        tags=["reviews"],
    )
    async def review_case(
        case_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> ReviewResult:
        """Run the parallel supplier review graph."""
        supplier_case = await get_authorized_case(case_id, principal, dependencies)
        require_analyst(principal)

        await dependencies.cases.set_status(case_id, CaseStatus.IN_REVIEW)
        await dependencies.cases.append_audit_event(
            AuditEventCreate(
                case_id=case_id,
                event_type="review.started",
                actor_id="system:review-workflow",
                payload={"initiated_by": principal.subject},
            )
        )
        try:
            result = await dependencies.workflow.run(supplier_case)
        except Exception as exc:
            await dependencies.cases.set_status(case_id, CaseStatus.REVIEW_FAILED)
            await dependencies.cases.append_audit_event(
                AuditEventCreate(
                    case_id=case_id,
                    event_type="review.failed",
                    actor_id="system:review-workflow",
                    payload={"error_type": type(exc).__name__},
                )
            )
            raise

        await persist_completed_review(result, dependencies)
        return result

    @app.post(
        "/api/v1/cases/{case_id}/review-executions",
        response_model=ReviewExecution,
        status_code=status.HTTP_201_CREATED,
        tags=["reviews"],
    )
    async def start_review_execution(
        case_id: UUID,
        payload: ReviewExecutionStart,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> ReviewExecution:
        """Start or replay an idempotent checkpointed review execution."""
        supplier_case = await get_authorized_case(case_id, principal, dependencies)
        require_analyst(principal)
        execution_id = dependencies.workflow.execution_id_for(
            supplier_case.tenant_id,
            supplier_case.id,
            payload.idempotency_key,
        )

        created = False
        try:
            execution = await dependencies.workflow.get_execution(
                supplier_case,
                execution_id,
            )
        except ReviewExecutionNotFoundError:
            created = True
            await dependencies.cases.set_status(case_id, CaseStatus.IN_REVIEW)
            await dependencies.cases.append_audit_event(
                AuditEventCreate(
                    case_id=case_id,
                    event_type="review.started",
                    actor_id="system:review-workflow",
                    payload={
                        "execution_id": str(execution_id),
                        "initiated_by": principal.subject,
                        "evidence_confirmation_required": (
                            payload.require_evidence_confirmation
                        ),
                    },
                )
            )
            try:
                execution = await dependencies.workflow.start(
                    supplier_case,
                    execution_id=execution_id,
                    require_evidence_confirmation=payload.require_evidence_confirmation,
                )
            except Exception as exc:
                await dependencies.cases.set_status(case_id, CaseStatus.REVIEW_FAILED)
                await dependencies.cases.append_audit_event(
                    AuditEventCreate(
                        case_id=case_id,
                        event_type="review.failed",
                        actor_id="system:review-workflow",
                        payload={
                            "execution_id": str(execution_id),
                            "error_type": type(exc).__name__,
                        },
                    )
                )
                raise

        if execution.status is ReviewExecutionStatus.COMPLETED:
            if execution.result is None:
                raise RuntimeError("Completed review execution has no result")
            await persist_completed_review(execution.result, dependencies)
        elif (
            execution.status is ReviewExecutionStatus.CANCELLED
            and supplier_case.status is CaseStatus.IN_REVIEW
        ):
            await dependencies.cases.set_status(case_id, CaseStatus.NEW)
            await dependencies.cases.append_audit_event(
                AuditEventCreate(
                    case_id=case_id,
                    event_type="review.cancelled",
                    actor_id=principal.subject,
                    payload={"execution_id": str(execution_id)},
                )
            )
        elif created and execution.status is ReviewExecutionStatus.AWAITING_INPUT:
            await dependencies.cases.append_audit_event(
                AuditEventCreate(
                    case_id=case_id,
                    event_type="review.awaiting_input",
                    actor_id="system:review-workflow",
                    payload={"execution_id": str(execution_id)},
                )
            )
        return execution

    @app.get(
        "/api/v1/cases/{case_id}/review-executions/{execution_id}",
        response_model=ReviewExecution,
        tags=["reviews"],
    )
    async def get_review_execution(
        case_id: UUID,
        execution_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> ReviewExecution:
        """Return the current checkpointed execution state."""
        supplier_case = await get_authorized_case(case_id, principal, dependencies)
        try:
            return await dependencies.workflow.get_execution(supplier_case, execution_id)
        except ReviewExecutionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post(
        "/api/v1/cases/{case_id}/review-executions/{execution_id}/resume",
        response_model=ReviewExecution,
        tags=["reviews"],
    )
    async def resume_review_execution(
        case_id: UUID,
        execution_id: UUID,
        payload: EvidenceConfirmation,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> ReviewExecution:
        """Resume an evidence-confirmation interrupt using the same durable thread."""
        supplier_case = await get_authorized_case(case_id, principal, dependencies)
        require_analyst(principal)
        try:
            current = await dependencies.workflow.get_execution(supplier_case, execution_id)
            execution = await dependencies.workflow.resume(
                supplier_case,
                execution_id,
                payload,
            )
        except ReviewExecutionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ReviewExecutionStateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        if current.status is ReviewExecutionStatus.AWAITING_INPUT:
            await dependencies.cases.append_audit_event(
                AuditEventCreate(
                    case_id=case_id,
                    event_type="review.resumed",
                    actor_id=principal.subject,
                    payload={
                        "execution_id": str(execution_id),
                        "decision": payload.decision.value,
                    },
                )
            )
        if execution.status is ReviewExecutionStatus.COMPLETED:
            if execution.result is None:
                raise RuntimeError("Completed review execution has no result")
            await persist_completed_review(execution.result, dependencies)
        elif execution.status is ReviewExecutionStatus.CANCELLED and (
            current.status is ReviewExecutionStatus.AWAITING_INPUT
            or supplier_case.status is CaseStatus.IN_REVIEW
        ):
            await dependencies.cases.set_status(case_id, CaseStatus.NEW)
            await dependencies.cases.append_audit_event(
                AuditEventCreate(
                    case_id=case_id,
                    event_type="review.cancelled",
                    actor_id=principal.subject,
                    payload={"execution_id": str(execution_id)},
                )
            )
        return execution

    @app.get(
        "/api/v1/cases/{case_id}/reviews/latest",
        response_model=ReviewResult,
        tags=["reviews"],
    )
    async def get_latest_review(
        case_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> ReviewResult:
        """Return the latest persisted review for a case."""
        await get_authorized_case(case_id, principal, dependencies)
        try:
            return await dependencies.cases.get_latest_review(case_id)
        except (CaseNotFoundError, ReviewNotFoundError) as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get(
        "/api/v1/cases/{case_id}/audit-events",
        response_model=list[AuditEvent],
        tags=["audit"],
    )
    async def list_audit_events(
        case_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> list[AuditEvent]:
        """Return the append-only case audit history."""
        await get_authorized_case(case_id, principal, dependencies)
        return await dependencies.cases.list_audit_events(case_id)

    @app.post(
        "/api/v1/cases/{case_id}/documents/index",
        response_model=IndexResult,
        tags=["retrieval"],
    )
    async def index_case_documents(
        case_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> IndexResult:
        """Chunk and embed every document attached to a case."""
        supplier_case = await get_authorized_case(case_id, principal, dependencies)
        if not principal.has_role(Role.ANALYST):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The analyst role is required",
            )
        result = await dependencies.retrieval.index_case(supplier_case)
        await dependencies.cases.append_audit_event(
            AuditEventCreate(
                case_id=case_id,
                event_type="documents.indexed",
                actor_id="system:retrieval",
                payload={
                    "document_count": result.document_count,
                    "chunk_count": result.chunk_count,
                    "embedding_backend": result.embedding_backend,
                },
            )
        )
        return result

    @app.get(
        "/api/v1/cases/{case_id}/evidence/search",
        response_model=list[RetrievedChunk],
        tags=["retrieval"],
    )
    async def search_case_evidence(
        case_id: UUID,
        query: str = Query(min_length=2, max_length=500),
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> list[RetrievedChunk]:
        """Retrieve semantically similar evidence scoped to one case."""
        await get_authorized_case(case_id, principal, dependencies)
        return await dependencies.retrieval.search(
            case_id=case_id,
            query=query,
            limit=dependencies.settings.retrieval_limit,
        )

    @app.post(
        "/api/v1/cases/{case_id}/actions",
        response_model=GovernedAction,
        status_code=status.HTTP_201_CREATED,
        tags=["governance"],
    )
    async def propose_case_action(
        case_id: UUID,
        payload: ActionProposalRequest,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> GovernedAction:
        """Derive an approval-gated enterprise action from the latest review."""
        supplier_case = await get_authorized_case(case_id, principal, dependencies)
        try:
            review = await dependencies.cases.get_latest_review(case_id)
            action = await dependencies.governance.propose(
                supplier_case=supplier_case,
                review=review,
                request=payload,
                principal=principal,
            )
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except AuthorizationDeniedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return action

    @app.get(
        "/api/v1/actions/{action_id}",
        response_model=GovernedAction,
        tags=["governance"],
    )
    async def get_action(
        action_id: UUID,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> GovernedAction:
        """Return a governed action within the caller's tenant."""
        try:
            return await dependencies.governance.get(action_id, principal)
        except (ActionNotFoundError, AuthorizationDeniedError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Governed action {action_id} was not found",
            ) from exc

    @app.post(
        "/api/v1/actions/{action_id}/decision",
        response_model=GovernedAction,
        tags=["governance"],
    )
    async def decide_action(
        action_id: UUID,
        payload: ApprovalRequest,
        principal: Principal = Depends(get_principal),
        dependencies: ApplicationContainer = Depends(get_container),
    ) -> GovernedAction:
        """Approve or reject a proposal under separation-of-duties policy."""
        try:
            action = await dependencies.governance.decide(
                action_id=action_id,
                request=payload,
                principal=principal,
            )
        except ActionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except AuthorizationDeniedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ActionStateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return action

    return app


app = create_app()
