# Architecture overview

## Product boundary

The Supplier Assurance Copilot augments an existing supplier-onboarding process. It gathers
evidence and proposes a decision, but the enterprise remains the system of record and a human
remains accountable for externally visible changes.

## Runtime ownership

| Concern | Owner |
| --- | --- |
| HTTP requests, authentication, and tenancy | FastAPI |
| Cognitive review state, parallel specialists, and clarification | LangGraph |
| Formal approval, SLA timers, and business completion | AWS Step Functions |
| Agent deployment and session isolation | Bedrock AgentCore Runtime |
| Enterprise tool discovery and authorization | AgentCore Gateway and MCP |
| Model policy, aliases, budgets, and fallbacks | LiteLLM plus native provider adapters |
| Exact workflow state and audit records | PostgreSQL |
| Enterprise document retrieval | pgvector initially, OpenSearch hybrid at scale |

## Containerization boundary

Containerization is an execution and packaging concern, not a new application component. Local
services, tests, migrations, smoke clients, CI checks, and CDK/AWS tooling all run in containers.
This does not change the ownership or request flow below: LangGraph still owns cognitive review,
Step Functions still owns formal approval, PostgreSQL remains authoritative, and MCP remains the
approval-gated enterprise action boundary. The host only orchestrates containers with Docker
Compose and `make`.

## Request flow

1. An authenticated analyst creates a supplier case through the API.
2. The outer workflow records the case and starts the review runtime.
3. LangGraph runs security, legal, and financial specialists in parallel.
4. Every specialist returns schema-valid findings with source evidence.
5. Policy can pause before synthesis for an analyst evidence-confirmation interrupt. PostgreSQL
   holds the checkpoint until the same opaque execution thread is resumed.
6. A synthesis node produces a recommendation but no external writes.
7. The proposal, audit event, and workflow-start message commit in one PostgreSQL transaction.
8. An outbox worker starts Step Functions, then atomically stores the execution reference and
   publication marker. Step Functions pauses for formal human approval.
9. The MCP server receives only an action identifier, reloads the approved proposal, and invokes
   the narrow idempotent supplier-system adapter with its immutable stored arguments.
10. Audit events and trace identifiers link the business result to each model and tool operation.

## Identity and action governance

Production requests use asymmetric OIDC access tokens with a fixed algorithm allowlist plus
issuer, audience, expiry, subject, and tenant validation. Local development uses explicit identity
headers and never silently creates an anonymous user. Every case and action is tenant-owned.

Agents produce recommendations, not write arguments. The application derives a fixed
`supplier.set_onboarding_decision` proposal from a persisted review. An analyst proposes it, a
different approver decides it, and an executor can apply it only after approval. Idempotency keys
are unique per tenant. MCP annotations describe the tool but are not treated as an authorization
control; server-side policy is authoritative.

PostgreSQL action transitions and their audit events share one transaction. Proposal creation also
enqueues the workflow start in that transaction. Delivery is intentionally at least once: workers
use short leases, stale-lease recovery, exponential retry, and a dead-letter terminal state. The
Step Functions execution name and enterprise idempotency key make replays safe.

LangGraph execution IDs are deterministic UUIDs derived from tenant, case, and caller-supplied
idempotency key. The API never trusts that identifier by itself: tenant and case ownership stored
inside the checkpoint must match the already-authorized case. Checkpoint state contains only
JSON-safe primitives, and strict MessagePack deserialization is enabled in the container runtime.

## Model access

The application uses capability-aware adapters rather than pretending all providers are
identical. Native OpenAI Responses and Bedrock Converse features remain available. LiteLLM owns
logical aliases and routine routing, while data-classification policy can require Bedrock-only
processing. The deterministic adapter is a local test double, not a production model.

## Data stores

- S3 stores originals and versioned Docling artifacts.
- PostgreSQL stores transactional state, document metadata, findings, approvals, audit events,
  LangGraph checkpoints, and smaller case-local vector collections.
- OpenSearch stores the larger policy and historical corpus when hybrid lexical/vector search is
  justified by scale and retrieval evaluations.

The same embedding collection should not be duplicated in both stores without a measured
retrieval or operational reason.

## Delivery slices

The current slices prove the typed API-to-graph path, durable case/review/audit persistence,
PostgreSQL graph pause/resume across runtime restarts, case-scoped pgvector retrieval, OIDC
boundaries, governed actions, MCP v2 execution, the Step Functions callback contract, and
transactional outbox recovery without live credentials. Subsequent slices add S3/queued Docling
ingestion, AWS deployment, and production observability.
