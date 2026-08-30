# Initial threat model

## Assets

- Supplier contracts, security reports, financial evidence, and personal data.
- Enterprise credentials and delegated OAuth tokens.
- Approval decisions, findings, tool proposals, and audit history.
- Prompts, model routing policy, and evaluation datasets.

## Trust boundaries

- Browser to FastAPI.
- API and agent runtime to model providers.
- Document ingestion to retrieval indexes.
- Agent runtime to MCP Gateway and enterprise systems.
- Human approval UI to the durable workflow.

## Priority threats and controls

| Threat | Initial control |
| --- | --- |
| Prompt injection in supplier documents | Treat content as evidence; separate instructions; validate every output |
| Cross-tenant retrieval | Apply tenant and ACL filters before retrieval and ranking |
| Excessive agency | Models emit proposals only; humans approve; deterministic executor performs writes |
| Tool argument injection | Strict JSON Schema and Pydantic validation; no additional properties |
| Duplicate writes after retry | Idempotency keys and append-only execution records |
| Credential disclosure | AgentCore Identity, Secrets Manager, redaction, and no tokens in graph state |
| Model fallback violating residency policy | Classification-aware allowlists and deny-by-default fallback |
| Hallucinated evidence | Stable evidence IDs, quotations, source coordinates, and citation verification |
| Cross-tenant case or action access | Verified tenant claim, tenant-owned records, and not-found responses across tenants |
| Self-approval or approval bypass | Role checks, proposer/approver separation, immutable proposal state |
| Model changes write arguments | MCP accepts only an action ID and reloads approved arguments server-side |
| Duplicate external writes | Tenant-scoped idempotency key plus idempotent supplier-system adapter |
| Lost workflow start after database commit | Transactional outbox, leased delivery, stale-lock recovery, and dead-letter state |
| Worker crash after external call | Stable Step Functions execution name and at-least-once replay |
| Algorithm confusion in JWT validation | Fixed asymmetric algorithm allowlist; verified issuer, audience, expiry, and JWKS key |

## Release invariants

- Unauthorized write count is zero.
- Cross-tenant evidence count is zero.
- Every material finding has valid evidence or is explicitly labeled missing evidence.
- A model response alone can never complete a supplier-system mutation.
