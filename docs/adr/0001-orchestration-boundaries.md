# ADR 0001: Separate cognitive and business workflow orchestration

Status: accepted

## Context

LangGraph and Step Functions both support durable workflows. Allowing them to manage the same
approval and retry state would create ambiguous recovery behavior and duplicate state.

## Decision

LangGraph owns the bounded cognitive review: parallel specialists, evidence gathering,
clarification, critique, and recommendation. Step Functions owns the outer enterprise process:
case lifecycle, SLA waits, formal approval, external execution, and completion.

LangGraph can interrupt for analyst clarification. Formal approval uses either a Step Functions
callback task token with a deployed approval bridge or a deterministic poll of committed approval
state. Step Functions stores identifiers and status, not the full model context.

## Consequences

- Recovery ownership is explicit.
- Long-running business waits do not hold agent compute.
- Graph state can evolve independently of enterprise process state.
- Integration tests must verify idempotency across the boundary.
