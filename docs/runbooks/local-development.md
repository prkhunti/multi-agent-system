# Local development runbook

## Host requirements

Install Docker Desktop or Docker Engine with Compose v2, plus `make`. Do not create a project
virtual environment or install Python, Node.js, CDK, or the AWS CLI for this repository. Those
tools run in purpose-built containers.

## Fast path

From the repository root:

```bash
make env
make dev-build
```

Open <http://localhost:8000/docs>. The API source is bind-mounted and Uvicorn reload runs inside
the API container. Stop the foreground stack with `Ctrl+C`, or run `make dev-down` from another
terminal.

`scripts/api-dev.sh` remains as a convenience wrapper, but it now starts the same Docker Compose
development service and never invokes a host Python interpreter.

The deterministic backend is expected in local development and CI. It makes no model-provider
network calls.

## Governed flow

Run:

```bash
make governance-up
make smoke
```

This starts FastAPI, PostgreSQL with pgvector, Redis, the outbox worker, and the approval-gated MCP
service. The smoke client also runs in Docker and verifies persistence, vector retrieval,
checkpointed review pause/resume, independent approval, MCP execution, and audit history. Stop the
stack with `make governance-down`.

PostgreSQL is published to `localhost:55432` by default. Set `POSTGRES_HOST_PORT` to override it.
The API container applies pending migrations after PostgreSQL becomes healthy. Manual migration
commands are also containerized:

```bash
make migrate
make migrate-new MSG="describe the schema change"
```

Use `make test-checkpoint` to prove a paused LangGraph execution can be loaded and resumed by a
new runtime using PostgreSQL. Use `make test-mcp` for the transport-free MCP protocol test.

## Quality and infrastructure checks

All checks run in isolated tool containers with the repository bind-mounted:

```bash
make lint
make typecheck
make test
make test-eval
make iac-test
```

`make format` writes formatting changes back through the bind mount. `make clean` removes caches
from inside the same tooling container.

## Optional services

OpenSearch remains opt-in:

```bash
docker compose -f infra/docker-compose.yaml --profile search up -d
```

LiteLLM is isolated because its proxy dependencies must not constrain the MCP v2 runtime. Set
`LITELLM_MASTER_KEY` and `LITELLM_BEDROCK_MODEL`, then run `make gateway-up`. Its endpoint is
<http://localhost:4000>.

Do not enable a live provider unless its credentials are available outside tracked files and its
evaluation configuration has been selected. Never print provider keys while diagnosing setup.
