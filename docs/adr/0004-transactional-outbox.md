# ADR 0004: Deliver workflow side effects through a transactional outbox

Status: accepted

## Context

Starting Step Functions inside the proposal request created two failure windows. A database
failure after the external call could orphan a workflow, while a process failure after committing
the action could permanently lose the workflow start. Audit events were also committed separately
from the action transitions they described.

## Decision

PostgreSQL proposal creation writes the governed action, its audit event, and an
`approval.workflow.start` outbox message in one transaction. Approval and execution transitions
write their audit events in the same transaction as the state change.

An independent worker claims due messages using `FOR UPDATE SKIP LOCKED`, commits a short lease,
and performs the external call without holding a database transaction open. Success atomically
stores both the Step Functions execution reference and the outbox publication marker. Failures use
bounded exponential retry; expired leases are reclaimable; exhausted messages remain available
for investigation in a dead-letter state.

Delivery is at least once. Step Functions uses a deterministic execution name, and downstream
enterprise writes retain their tenant-scoped idempotency key.

## Consequences

- A committed proposal cannot lose its approval-workflow start.
- Audit history cannot disagree with a committed action transition.
- A crash after the external call can invoke the adapter again, so every handler must be
  idempotent.
- Operators must monitor retry age and dead-letter counts and provide an intentional replay
  procedure.
