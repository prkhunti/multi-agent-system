"""Application dependency container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from packages.governance.repositories import (
    ActionRepository,
    InMemoryActionRepository,
    SqlAlchemyActionRepository,
)
from packages.governance.service import GovernanceService, InMemorySupplierSystem
from packages.graphs.supplier_review import SupplierReviewWorkflow
from packages.identity.auth import Authenticator, DevelopmentAuthenticator, OIDCAuthenticator
from packages.model_gateway.base import ModelBackend
from packages.model_gateway.factory import create_model_backend
from packages.persistence.database import create_database_engine, create_session_factory
from packages.persistence.repositories import (
    CaseRepository,
    InMemoryCaseRepository,
    SqlAlchemyCaseRepository,
)
from packages.persistence.secrets import resolve_database_settings
from packages.retrieval.factory import create_embedding_provider
from packages.retrieval.service import (
    InMemoryRetrievalService,
    PgVectorRetrievalService,
    RetrievalService,
)
from packages.settings import Settings, get_settings
from packages.workflows.factory import create_approval_workflow


@dataclass(slots=True)
class ApplicationContainer:
    """Runtime dependencies shared by API handlers."""

    settings: Settings
    cases: CaseRepository
    workflow: SupplierReviewWorkflow
    retrieval: RetrievalService
    authenticator: Authenticator
    governance: GovernanceService
    model_backend: ModelBackend = field(repr=False)
    database_engine: AsyncEngine | None = None
    checkpoint_context: Any | None = field(default=None, repr=False)

    async def start(self) -> None:
        """Initialize the production checkpointer before accepting requests."""
        if self.settings.checkpoint_backend != "postgres" or self.checkpoint_context is not None:
            return

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        context = AsyncPostgresSaver.from_conn_string(self.settings.langgraph_database_url)
        checkpointer = await context.__aenter__()
        try:
            await checkpointer.setup()
        except Exception:
            await context.__aexit__(None, None, None)
            raise
        self.workflow = SupplierReviewWorkflow(self.model_backend, checkpointer)
        self.checkpoint_context = context

    async def close(self) -> None:
        """Dispose process resources owned by the container."""
        if self.checkpoint_context is not None:
            await self.checkpoint_context.__aexit__(None, None, None)
            self.checkpoint_context = None
        if self.database_engine is not None:
            await self.database_engine.dispose()


def create_container(settings: Settings | None = None) -> ApplicationContainer:
    """Create a new dependency container."""
    resolved_settings = resolve_database_settings(settings or get_settings())
    backend = create_model_backend(resolved_settings)
    embeddings = create_embedding_provider(resolved_settings)
    engine: AsyncEngine | None = None
    cases: CaseRepository
    actions: ActionRepository
    if resolved_settings.repository_backend == "postgres":
        engine = create_database_engine(resolved_settings.database_url)
        sessions = create_session_factory(engine)
        cases = SqlAlchemyCaseRepository(sessions)
        actions = SqlAlchemyActionRepository(sessions)
        retrieval: RetrievalService = PgVectorRetrievalService(sessions, embeddings)
    else:
        cases = InMemoryCaseRepository()
        actions = InMemoryActionRepository(audit_writer=cases.append_audit_event)
        retrieval = InMemoryRetrievalService(embeddings)

    authenticator: Authenticator
    if resolved_settings.auth_mode == "oidc":
        authenticator = OIDCAuthenticator(
            issuer=resolved_settings.oidc_issuer,
            audience=resolved_settings.oidc_audience,
            jwks_url=resolved_settings.oidc_jwks_url,
            algorithm=resolved_settings.oidc_algorithm,
        )
    else:
        authenticator = DevelopmentAuthenticator()

    if (
        resolved_settings.approval_workflow_backend == "step_functions"
        and resolved_settings.repository_backend != "postgres"
    ):
        raise ValueError("Step Functions requires PostgreSQL transactional outbox persistence")
    approval_workflow = create_approval_workflow(resolved_settings)

    governance = GovernanceService(
        repository=actions,
        approval_workflow=approval_workflow,
        supplier_system=InMemorySupplierSystem(),
    )
    return ApplicationContainer(
        settings=resolved_settings,
        cases=cases,
        workflow=SupplierReviewWorkflow(backend),
        retrieval=retrieval,
        authenticator=authenticator,
        governance=governance,
        model_backend=backend,
        database_engine=engine,
    )
