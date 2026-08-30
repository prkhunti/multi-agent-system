# ADR 0005: Persist cognitive workflow state with LangGraph PostgreSQL checkpoints

Status: accepted

## Context

The review graph originally lived only for the duration of one HTTP request. A process restart
lost all intermediate specialist work, and the graph could not safely pause for analyst input.
Creating a parallel application-owned execution table would duplicate the graph's current node,
interrupt, and state lifecycle and could drift from the actual checkpoint.

## Decision

LangGraph remains the sole owner of cognitive workflow execution state. Production compiles the
graph with `AsyncPostgresSaver`; local tests use `InMemorySaver`. API startup runs the checkpointer
setup migration and holds its async connection context for the application lifetime.

Every durable start requires a client idempotency key. The service derives an opaque execution
UUID from tenant, case, and that key, then supplies it as LangGraph's `thread_id`. Tenant ID, case
ID, inputs, specialist findings, timestamps, and gate state are stored as JSON-safe primitives in
the checkpoint. Loading or resuming requires both an already-authorized case and an exact ownership
match inside the checkpoint.

The evidence-confirmation node uses a dynamic interrupt. A schema-valid `Command(resume=...)`
continues the same thread. Rejecting the evidence cancels the cognitive execution; confirming it
allows synthesis. Completed review results are persisted idempotently to the business tables.
Formal approval and external writes remain outside LangGraph under Step Functions and MCP policy.

## Consequences

- Specialist outputs survive API restarts and are not repeated when resuming after the gate.
- Start and terminal resume delivery can be retried without creating a second review.
- Checkpoint tables are managed by the official saver rather than Alembic business migrations.
- Database compromise could expose sensitive review state, so database encryption/access control
  and strict MessagePack deserialization are required.
- A model call interrupted before its node checkpoint commits may run again; provider calls and
  downstream side effects must therefore retain idempotency controls.
