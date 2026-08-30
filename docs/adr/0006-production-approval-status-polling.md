# ADR 0006: Poll committed approval state in the production workflow

Status: accepted

## Context

The original callback-token state machine publishes to SQS, but the repository does not contain an
enterprise approval-queue consumer that can safely retain and complete the task token. Deploying
that definition would leave production executions waiting on an undeployed component.

## Decision

The production CDK stack uses a Standard Step Functions workflow that invokes a private Lambda
every five minutes. The Lambda accepts a schema-validated action identifier, reads only the
committed governed-action status from PostgreSQL, and returns a schema-validated status. The OIDC
approval API remains the human decision boundary. The workflow succeeds for `approved` or
`executed`, fails for `rejected` or an unexpected terminal state, and times out after 30 days.

The callback-token ASL remains available for a future enterprise approval bridge, but it is not
deployed until that bridge exists.

## Consequences

- The production workflow is complete with components deployed by this repository.
- No task token is exposed to the approval API or a browser.
- Approval latency is up to five minutes.
- Polling consumes Step Functions transitions and Lambda invocations while an action is pending.
- PostgreSQL remains the source of truth for the human decision.
