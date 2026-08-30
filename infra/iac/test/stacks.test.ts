import assert from "node:assert/strict";
import { test } from "node:test";
import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { readDemoConfig } from "../lib/config";
import { DemoStack } from "../lib/demo-stack";
import { ProductionStack } from "../lib/production-stack";

test("demo configuration refuses deployment without the explicit account acknowledgement", () => {
  const app = new cdk.App({
    context: {
      budgetEmail: "owner@example.com",
      environment: "demo",
      freeTierAcknowledged: "false",
    },
  });

  assert.throws(() => readDemoConfig(app), /freeTierAcknowledged=true/);
});

test("demo contains only the free-plan allowlist and deterministic settings", () => {
  const app = new cdk.App();
  const stack = new DemoStack(app, "DemoTest", {
    config: { budgetEmail: "owner@example.com" },
  });
  const template = Template.fromStack(stack);
  const json = template.toJSON();
  const types = new Set(
    Object.values(json.Resources as Record<string, { Type: string }>).map(
      (resource) => resource.Type,
    ),
  );

  for (const forbidden of [
    "AWS::Bedrock::",
    "AWS::BedrockAgentCore::",
    "AWS::EC2::",
    "AWS::ECS::",
    "AWS::ElasticLoadBalancingV2::",
    "AWS::OpenSearchService::",
    "AWS::RDS::",
  ]) {
    assert.equal([...types].some((type) => type.startsWith(forbidden)), false);
  }
  template.hasResourceProperties("AWS::Lambda::Function", {
    Environment: {
      Variables: {
        MODEL_BACKEND: "deterministic",
        REPOSITORY_BACKEND: "memory",
        CHECKPOINT_BACKEND: "memory",
      },
    },
  });
});

test("production includes durable, multi-AZ, approval and AgentCore resources", () => {
  const app = new cdk.App();
  const stack = new ProductionStack(app, "ProductionTest", {
    env: { account: "123456789012", region: "us-east-1" },
    config: {
      apiDomainName: "supplier-api.example.com",
      availabilityZones: ["us-east-1a", "us-east-1b", "us-east-1c"],
      bedrockModelId: "anthropic.claude-3-5-sonnet-20241022-v2:0",
      budgetEmail: "owner@example.com",
      certificateArn:
        "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000",
      hostedZoneId: "Z0123456789ABC",
      hostedZoneName: "example.com",
      monthlyBudgetUsd: 500,
      oidcAudience: "supplier-assurance-api",
      oidcIssuer: "https://id.example.com/",
      oidcJwksUrl: "https://id.example.com/.well-known/jwks.json",
    },
  });
  const template = Template.fromStack(stack);

  template.hasResourceProperties("AWS::RDS::DBInstance", {
    MultiAZ: true,
    DeletionProtection: true,
    StorageEncrypted: true,
  });
  template.resourceCountIs("AWS::EC2::NatGateway", 3);
  template.resourceCountIs("AWS::BedrockAgentCore::Runtime", 1);
  template.resourceCountIs("AWS::BedrockAgentCore::RuntimeEndpoint", 1);
  template.resourceCountIs("AWS::Lambda::Function", 1);
  template.resourceCountIs("AWS::SQS::Queue", 0);
  template.hasResourceProperties("AWS::StepFunctions::StateMachine", {
    StateMachineType: "STANDARD",
  });
  template.resourceCountIs("AWS::WAFv2::WebACL", 1);
  template.resourceCountIs("AWS::Route53::RecordSet", 1);
});
