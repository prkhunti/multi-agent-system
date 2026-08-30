# AWS infrastructure deployments: strict how-to

Choose exactly one deployment. Never mix demo and production context. The host needs Docker,
Compose, `make`, and an existing AWS credentials file; Node.js, npm, CDK, Python, and the AWS CLI
all run in containers.

`make cdk` mounts `~/.aws` read-only and mounts the Docker socket because CDK builds the existing
Lambda, ECS, and AgentCore image assets. This packaging change does not add or remove an AWS
application component.

## Mandatory demo: Free Plan / Free Tier eligible only

The demo stack is ephemeral. It creates API Gateway HTTP API, one Lambda container, CloudWatch
Logs, IAM, and a USD 1 budget alarm. It forces the deterministic model, in-memory repository, and
in-memory checkpoints. Its synthesis guard rejects Bedrock, AgentCore, VPC, NAT, RDS, ECS, load
balancers, OpenSearch, ElastiCache, KMS, Secrets Manager, and Step Functions.

AWS does not provide a universal permanent zero-cost guarantee. Continue only while the account
has active Free Plan credits or applicable Free Tier allowances. Overages are billable. A budget
is an alert, not a hard spending cap.

### 1. Pass the account gate

1. Sign in through [AWS Free Tier](https://aws.amazon.com/free/).
2. Confirm active Free Plan credits or the applicable allowances.
3. Confirm the account has no unrelated running resources.
4. Stop if any check fails.

### 2. Configure one shell session

From the repository root:

```bash
export AWS_PROFILE="portfolio-demo"
export AWS_REGION="us-east-1"
export BUDGET_EMAIL="you@example.com"
export AWS_ACCOUNT_ID="$(make -s aws AWS_ARGS="sts get-caller-identity --profile $AWS_PROFILE --query Account --output text")"
make aws AWS_ARGS="sts get-caller-identity --profile $AWS_PROFILE"
```

### 3. Test and bootstrap

CDK bootstrap stores deployment assets in S3 and ECR. Keep ECR storage within the account's
applicable allowance and remove unused assets.

```bash
make iac-test
make cdk CDK_ARGS="bootstrap aws://$AWS_ACCOUNT_ID/$AWS_REGION --profile $AWS_PROFILE"
```

### 4. Pass the mandatory synthesis gate

```bash
make cdk CDK_ARGS="synth SupplierAssuranceDemo --profile $AWS_PROFILE -c environment=demo -c freeTierAcknowledged=true -c budgetEmail=$BUDGET_EMAIL"
```

Inspect `infra/iac/cdk.out/SupplierAssuranceDemo.template.json`. It must not contain any resource
whose type begins with the following production-only namespaces:

```text
AWS::Bedrock
AWS::BedrockAgentCore
AWS::EC2
AWS::ECS
AWS::ElasticLoadBalancing
AWS::OpenSearch
AWS::RDS
AWS::ElastiCache
AWS::KMS
AWS::SecretsManager
AWS::StepFunctions
```

Stop if the mandatory CDK test or this inspection fails.

### 5. Deploy and verify

```bash
make cdk CDK_ARGS="deploy SupplierAssuranceDemo --profile $AWS_PROFILE --require-approval broadening -c environment=demo -c freeTierAcknowledged=true -c budgetEmail=$BUDGET_EMAIL"
export DEMO_API_URL="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name SupplierAssuranceDemo --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text")"
curl --fail --silent --show-error "$DEMO_API_URL/health"
```

The response must contain `"model_backend":"deterministic"` and
`"repository_backend":"memory"`. Use synthetic data only; state can disappear on any cold start
or deployment.

### 6. Teardown immediately after the demonstration

```bash
make cdk CDK_ARGS="destroy SupplierAssuranceDemo --profile $AWS_PROFILE --force -c environment=demo -c freeTierAcknowledged=true -c budgetEmail=$BUDGET_EMAIL"
make aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name SupplierAssuranceDemo"
```

The final command must return a stack-not-found error. Review Billing and Cost Management before
ending the session.

## Production deployment: intentionally billable

The production stack creates three NAT gateways, a public Application Load Balancer, WAF, private
ECS Fargate API/outbox/MCP services, Multi-AZ RDS PostgreSQL, KMS, immutable S3 evidence storage,
Standard Step Functions approvals with a private database-backed status poller, Bedrock
permissions, and a VPC-attached Bedrock AgentCore Runtime plus stable endpoint. It is not Free
Tier code.

### 1. Pass the production gate

1. Obtain an ACM certificate in the deployment region.
2. Select a public Route 53 hosted zone and confirm the certificate covers the exact API domain.
3. Configure an OIDC issuer whose tokens contain `sub`, `tenant_id`, and roles.
4. Confirm AgentCore Runtime and VPC support in three selected Availability Zones.
5. Confirm the network service-linked role exists or can be created.
6. Enable the selected Bedrock model in the deployment region.
7. Approve the budget, data retention, backup, and incident policies.
8. Stop if any check fails.

### 2. Configure one shell session

```bash
export AWS_PROFILE="supplier-production"
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="$(make -s aws AWS_ARGS="sts get-caller-identity --profile $AWS_PROFILE --query Account --output text")"
export AVAILABILITY_ZONES="us-east-1a,us-east-1b,us-east-1c"
export BUDGET_EMAIL="platform-alerts@example.com"
export MONTHLY_BUDGET_USD="500"
export CERTIFICATE_ARN="arn:aws:acm:us-east-1:123456789012:certificate/replace-me"
export HOSTED_ZONE_ID="replace-with-route53-zone-id"
export HOSTED_ZONE_NAME="example.com"
export API_DOMAIN_NAME="supplier-api.example.com"
export OIDC_ISSUER="https://id.example.com/"
export OIDC_AUDIENCE="supplier-assurance-api"
export OIDC_JWKS_URL="https://id.example.com/.well-known/jwks.json"
export BEDROCK_MODEL_ID="replace-with-approved-model-id"
make aws AWS_ARGS="sts get-caller-identity --profile $AWS_PROFILE"
```

### 3. Test, bootstrap, synthesize, and diff

Use the same context for every production command:

```bash
export CDK_CONTEXT="-c environment=production -c productionAcknowledged=true -c availabilityZones=$AVAILABILITY_ZONES -c budgetEmail=$BUDGET_EMAIL -c monthlyBudgetUsd=$MONTHLY_BUDGET_USD -c certificateArn=$CERTIFICATE_ARN -c hostedZoneId=$HOSTED_ZONE_ID -c hostedZoneName=$HOSTED_ZONE_NAME -c apiDomainName=$API_DOMAIN_NAME -c oidcIssuer=$OIDC_ISSUER -c oidcAudience=$OIDC_AUDIENCE -c oidcJwksUrl=$OIDC_JWKS_URL -c bedrockModelId=$BEDROCK_MODEL_ID"
make iac-test
make cdk CDK_ARGS="bootstrap aws://$AWS_ACCOUNT_ID/$AWS_REGION --profile $AWS_PROFILE"
make cdk CDK_ARGS="synth SupplierAssuranceProduction --profile $AWS_PROFILE $CDK_CONTEXT"
make cdk CDK_ARGS="diff SupplierAssuranceProduction --profile $AWS_PROFILE $CDK_CONTEXT"
```

Stop unless the reviewed diff contains exactly one production stack and the expected billable
resources. Do not store secrets in CDK context.

### 4. Deploy

```bash
make cdk CDK_ARGS="deploy SupplierAssuranceProduction --profile $AWS_PROFILE --require-approval broadening $CDK_CONTEXT"
```

### 5. Run the one-off database migration

Read the four migration outputs through the containerized AWS CLI:

```bash
export STACK_NAME="SupplierAssuranceProduction"
export CLUSTER_NAME="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`ClusterName\`].OutputValue' --output text")"
export MIGRATION_TASK="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`MigrationTaskDefinition\`].OutputValue' --output text")"
export MIGRATION_SUBNETS="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`MigrationSubnets\`].OutputValue' --output text")"
export MIGRATION_SECURITY_GROUP="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`MigrationSecurityGroup\`].OutputValue' --output text")"
```

Run the migration task. The `python` process named here runs inside the deployed API container,
not on the workstation:

```bash
export MIGRATION_TASK_ARN="$(make -s aws AWS_ARGS="ecs run-task --profile $AWS_PROFILE --region $AWS_REGION --cluster $CLUSTER_NAME --launch-type FARGATE --task-definition $MIGRATION_TASK --network-configuration awsvpcConfiguration={subnets=[$MIGRATION_SUBNETS],securityGroups=[$MIGRATION_SECURITY_GROUP],assignPublicIp=DISABLED} --overrides {\"containerOverrides\":[{\"name\":\"api\",\"command\":[\"python\",\"-m\",\"alembic\",\"-c\",\"pyproject.toml\",\"upgrade\",\"head\"]}]} --query 'tasks[0].taskArn' --output text")"
make aws AWS_ARGS="ecs wait tasks-stopped --profile $AWS_PROFILE --region $AWS_REGION --cluster $CLUSTER_NAME --tasks $MIGRATION_TASK_ARN"
make aws AWS_ARGS="ecs describe-tasks --profile $AWS_PROFILE --region $AWS_REGION --cluster $CLUSTER_NAME --tasks $MIGRATION_TASK_ARN --query 'tasks[0].containers[?name==\`api\`].exitCode' --output text"
```

The exit code must be `0`. Stop otherwise.

### 6. Verify the API and AgentCore

```bash
export PRODUCTION_API_URL="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text")"
curl --fail --silent --show-error "$PRODUCTION_API_URL/health"
export ACCESS_TOKEN="replace-with-short-lived-token"
curl --fail --silent --show-error -H "Authorization: Bearer $ACCESS_TOKEN" "$PRODUCTION_API_URL/api/v1/cases/replace-with-case-id"
```

Create a valid `SupplierCase` payload at `tmp/supplier-case.json`, then invoke the runtime through
the containerized AWS CLI:

```bash
export AGENT_RUNTIME_ARN="$(make -s aws AWS_ARGS="cloudformation describe-stacks --profile $AWS_PROFILE --region $AWS_REGION --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`AgentRuntimeArn\`].OutputValue' --output text")"
export RUNTIME_SESSION_ID="supplier-assurance-$(make -s uuid)-session"
make aws AWS_ARGS="bedrock-agentcore invoke-agent-runtime --profile $AWS_PROFILE --region $AWS_REGION --agent-runtime-arn $AGENT_RUNTIME_ARN --qualifier Production --runtime-session-id $RUNTIME_SESSION_ID --content-type application/json --accept application/json --payload fileb:///workspace/tmp/supplier-case.json /workspace/tmp/agent-response.json"
```

Inspect `tmp/agent-response.json`, then remove both temporary payloads. Never place production
evidence or access tokens in the repository.

### 7. Change production

1. Change only `infra/iac/` for infrastructure.
2. Run `make iac-test`, then containerized CDK synth and diff.
3. Review IAM broadening and resource replacements.
4. Run Alembic as a one-off ECS task before code that depends on the migration.
5. Deploy and verify API, AgentCore, Step Functions, the status Lambda, ECS, RDS, WAF, and alarms.
6. Record the CloudFormation change set and deployment evidence.

Do not run `cdk destroy` in production. RDS, KMS, log groups, and evidence storage are retained and
the database is deletion-protected.

## Current AWS references

- [AWS Free Tier](https://aws.amazon.com/free/)
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)
- [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
- [AgentCore Runtime CloudFormation](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html)
- [AgentCore HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
- [Step Functions pricing](https://aws.amazon.com/step-functions/pricing/)
