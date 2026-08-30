"""AgentCore HTTP contract for the production LangGraph workflow."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.dependencies import ApplicationContainer, create_container
from packages.schemas.cases import SupplierCase
from packages.schemas.reviews import ReviewResult

container: ApplicationContainer = create_container()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start and stop the durable LangGraph checkpointer."""
    await container.start()
    try:
        yield
    finally:
        await container.close()


app = FastAPI(title="Supplier Assurance AgentCore Runtime", lifespan=lifespan)


@app.get("/ping")
async def ping() -> dict[str, str]:
    """Implement the AgentCore health contract."""
    return {"status": "Healthy"}


@app.post("/invocations", response_model=ReviewResult)
async def invoke(supplier_case: SupplierCase) -> ReviewResult:
    """Run the LangGraph supplier review for a validated case payload."""
    return await container.workflow.run(supplier_case)
