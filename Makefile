COMPOSE     := docker compose -f infra/docker-compose.yaml
COMPOSE_DEV := docker compose -f infra/docker-compose.yaml \
                              -f infra/docker-compose.dev.yaml
RUN_TEST    := $(COMPOSE) --profile test run --rm -T --build --no-deps
RUN_IAC     := $(COMPOSE) --profile tooling run --rm -T --build --no-deps iac
RUN_IAC_AWS := $(COMPOSE) --profile tooling run --rm -T --build --no-deps \
	-v $(HOME)/.aws:/root/.aws:ro \
	-v /var/run/docker.sock:/var/run/docker.sock iac
RUN_AWS     := docker run --rm -i \
	-v $(HOME)/.aws:/root/.aws:ro \
	-v $(CURDIR):/workspace -w /workspace \
	-e AWS_PROFILE -e AWS_REGION -e AWS_DEFAULT_REGION \
	public.ecr.aws/aws-cli/aws-cli:2.36.34

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}' | sort

.PHONY: env
env: ## Create .env from the credential-free example when missing
	@test -f .env || cp .env.example .env

.PHONY: up
up: env ## Start all core services with production images
	$(COMPOSE) up -d

.PHONY: up-build
up-build: env ## Rebuild then start all core services
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop all services
	$(COMPOSE) down

.PHONY: down-v
down-v: ## Stop services and remove local volumes
	$(COMPOSE) down -v

.PHONY: build
build: env ## Build all Docker images
	$(COMPOSE) --profile governance --profile test --profile tooling build

.PHONY: logs
logs: ## Tail all service logs
	$(COMPOSE) logs -f

.PHONY: logs-api
logs-api: ## Tail API logs
	$(COMPOSE) logs -f api

.PHONY: logs-outbox
logs-outbox: ## Tail transactional outbox worker logs
	$(COMPOSE) logs -f outbox-worker

.PHONY: dev
dev: env ## Start the development stack with hot reload
	$(COMPOSE_DEV) up

.PHONY: dev-build
dev-build: env ## Rebuild and start the development stack
	$(COMPOSE_DEV) up --build

.PHONY: dev-down
dev-down: ## Stop the development stack
	$(COMPOSE_DEV) down

.PHONY: dev-logs
dev-logs: ## Tail development stack logs
	$(COMPOSE_DEV) logs -f

.PHONY: migrate
migrate: env ## Run all pending Alembic migrations in the API container
	$(COMPOSE) run --rm -T --build api python -m alembic -c pyproject.toml upgrade head

.PHONY: migrate-new
migrate-new: env ## Create a migration in the API container with MSG=description
	@test -n "$(MSG)" || (echo "MSG is required" && exit 1)
	$(COMPOSE_DEV) run --rm -T --build api \
		python -m alembic -c pyproject.toml revision --autogenerate -m "$(MSG)"

.PHONY: db-shell
db-shell: ## Open a PostgreSQL shell in the database container
	$(COMPOSE) exec postgres psql -U supplier -d supplier_assurance

.PHONY: shell-api
shell-api: ## Open a shell inside the API container
	$(COMPOSE) exec api sh

.PHONY: test
test: env ## Run all tests in Docker, including PostgreSQL checkpoint recovery
	$(COMPOSE) up -d postgres redis
	$(RUN_TEST) \
		-e LANGGRAPH_STRICT_MSGPACK=true \
		-e TEST_LANGGRAPH_DATABASE_URL=postgresql://supplier:supplier@postgres:5432/supplier_assurance \
		test python -m pytest

.PHONY: test-unit
test-unit: ## Run unit tests in Docker
	$(RUN_TEST) test python -m pytest tests/unit

.PHONY: test-integration
test-integration: env ## Run integration tests in Docker
	$(COMPOSE) up -d postgres redis
	$(RUN_TEST) \
		-e LANGGRAPH_STRICT_MSGPACK=true \
		-e TEST_LANGGRAPH_DATABASE_URL=postgresql://supplier:supplier@postgres:5432/supplier_assurance \
		test python -m pytest tests/integration

.PHONY: test-checkpoint
test-checkpoint: env ## Run the PostgreSQL checkpoint restart test in Docker
	$(COMPOSE) up -d postgres
	$(RUN_TEST) \
		-e LANGGRAPH_STRICT_MSGPACK=true \
		-e TEST_LANGGRAPH_DATABASE_URL=postgresql://supplier:supplier@postgres:5432/supplier_assurance \
		test python -m pytest tests/integration/test_postgres_checkpointer.py

.PHONY: test-eval
test-eval: ## Run deterministic evaluation tests in Docker
	$(RUN_TEST) test python -m pytest tests/eval

.PHONY: test-mcp
test-mcp: ## Run the in-process MCP protocol test in Docker
	$(RUN_TEST) test python -m pytest tests/integration/test_mcp_server.py

.PHONY: lint
lint: ## Run Ruff lint checks in Docker
	$(RUN_TEST) test python -m ruff check .

.PHONY: format
format: ## Format Python code with Ruff in Docker
	$(RUN_TEST) test python -m ruff format .

.PHONY: typecheck
typecheck: ## Run mypy type checks in Docker
	$(RUN_TEST) test python -m mypy apps packages tests scripts/smoke.py

.PHONY: governance-up
governance-up: env ## Start the core stack and MCP governance server
	$(COMPOSE) --profile governance up -d --build

.PHONY: governance-down
governance-down: ## Stop the MCP governance stack
	$(COMPOSE) --profile governance down

.PHONY: gateway-up
gateway-up: env ## Start the isolated LiteLLM gateway profile
	$(COMPOSE) --profile gateway up -d litellm

.PHONY: smoke
smoke: governance-up ## Exercise the governed API and MCP flow from Docker
	$(COMPOSE) --profile governance --profile test run --rm -T --build --no-deps \
		-e API_BASE_URL=http://api:8000 \
		-e MCP_BASE_URL=http://mcp-server:8001/mcp \
		test python scripts/smoke.py

.PHONY: clean
clean: ## Remove Python bytecode and tool caches from Docker
	$(RUN_TEST) test sh -c "find . -type d -name __pycache__ -prune -exec rm -rf {} + && \
		find . -type f -name '*.pyc' -delete && \
		rm -rf .pytest_cache .mypy_cache .ruff_cache"

.PHONY: iac-install
iac-install: ## Install pinned CDK dependencies in Docker
	$(RUN_IAC) npm ci

.PHONY: iac-test
iac-test: iac-install ## Test and type-check both AWS CDK stacks in Docker
	$(RUN_IAC) npm test
	$(RUN_IAC) npm run build

.PHONY: iac-synth-demo
iac-synth-demo: ## Synth the guarded demo stack in Docker (BUDGET_EMAIL required)
	@test -n "$(BUDGET_EMAIL)" || (echo "BUDGET_EMAIL is required" && exit 1)
	$(RUN_IAC_AWS) npx cdk synth SupplierAssuranceDemo \
		-c environment=demo -c freeTierAcknowledged=true -c budgetEmail="$(BUDGET_EMAIL)"

.PHONY: cdk
cdk: ## Run CDK in Docker with CDK_ARGS='...'
	@test -n "$(CDK_ARGS)" || (echo "CDK_ARGS is required" && exit 1)
	$(RUN_IAC_AWS) npx cdk $(CDK_ARGS)

.PHONY: aws
aws: ## Run AWS CLI v2 in Docker with AWS_ARGS='...'
	@test -n "$(AWS_ARGS)" || (echo "AWS_ARGS is required" && exit 1)
	@$(RUN_AWS) $(AWS_ARGS)

.PHONY: uuid
uuid: ## Generate a UUID in Docker
	@$(RUN_TEST) test python -c 'import uuid; print(uuid.uuid4())'
