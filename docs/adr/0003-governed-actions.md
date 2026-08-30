# ADR 0003: Models propose; deterministic services approve and execute

Status: accepted

## Context

Supplier onboarding eventually changes an enterprise system of record. Letting a model call that
write directly would combine interpretation, authorization, approval, and execution in one
unrecoverable step.

## Decision

The model produces only a typed review recommendation. Application code derives immutable tool
arguments and persists a tenant-owned action with an idempotency key. The proposer and approver
must be different principals. Step Functions owns the formal wait. The MCP server exposes only an
idempotent execution tool whose sole input is the approved action identifier; it reloads the
arguments and policy state rather than trusting model-supplied write fields.

MCP tool annotations are descriptive client hints, never authorization. OIDC claims, repository
state, role policy, tenant policy, and the execution adapter are the enforcement points.

## Consequences

- Prompt injection cannot alter the external write after proposal creation.
- Approval and execution are replayable and independently auditable.
- The enterprise adapter must enforce the same idempotency key across retries and replicas.
- State transitions and audit events share a database transaction; workflow starts use the
  transactional outbox defined by ADR 0004.
