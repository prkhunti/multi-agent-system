# ADR 0002: Preserve native model APIs behind a provider contract

Status: accepted

## Context

The project must demonstrate OpenAI Responses, Bedrock Converse, and LiteLLM without reducing
every provider to an unreliable lowest-common-denominator abstraction.

## Decision

Define one application-level ModelBackend protocol and maintain native provider implementations.
Use LiteLLM for logical aliases, spend controls, and approved fallbacks. Route requests using task
capabilities and data classification. Use the deterministic provider for tests.

## Consequences

- Native tools, guardrails, streaming, and structured output remain accessible.
- Provider conformance tests are required.
- Fallback is an explicit policy decision rather than automatic retry behavior.
