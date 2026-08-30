#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { DemoStack } from "../lib/demo-stack";
import { ProductionStack } from "../lib/production-stack";
import { readDemoConfig, readProductionConfig } from "../lib/config";

const app = new cdk.App();
const environment = app.node.tryGetContext("environment");
const stackEnvironment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

if (environment === "demo") {
  new DemoStack(app, "SupplierAssuranceDemo", {
    env: stackEnvironment,
    config: readDemoConfig(app),
  });
} else if (environment === "production") {
  new ProductionStack(app, "SupplierAssuranceProduction", {
    env: stackEnvironment,
    config: readProductionConfig(app),
  });
} else {
  throw new Error("Set exactly one environment context: -c environment=demo|production");
}
