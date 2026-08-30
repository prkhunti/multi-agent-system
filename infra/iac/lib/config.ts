import { App } from "aws-cdk-lib";

export interface DemoConfig {
  readonly budgetEmail: string;
}

export interface ProductionConfig {
  readonly apiDomainName: string;
  readonly availabilityZones: string[];
  readonly bedrockModelId: string;
  readonly budgetEmail: string;
  readonly certificateArn: string;
  readonly hostedZoneId: string;
  readonly hostedZoneName: string;
  readonly monthlyBudgetUsd: number;
  readonly oidcAudience: string;
  readonly oidcIssuer: string;
  readonly oidcJwksUrl: string;
}

function requiredString(app: App, name: string): string {
  const value = app.node.tryGetContext(name);
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Missing required CDK context: -c ${name}=...`);
  }
  return value;
}

function requireAcknowledgement(app: App, name: string): void {
  if (app.node.tryGetContext(name) !== "true") {
    throw new Error(`Deployment is blocked until -c ${name}=true is supplied`);
  }
}

export function readDemoConfig(app: App): DemoConfig {
  if (app.node.tryGetContext("environment") !== "demo") {
    throw new Error("Demo configuration requires -c environment=demo");
  }
  requireAcknowledgement(app, "freeTierAcknowledged");
  return { budgetEmail: requiredString(app, "budgetEmail") };
}

export function readProductionConfig(app: App): ProductionConfig {
  if (app.node.tryGetContext("environment") !== "production") {
    throw new Error("Production configuration requires -c environment=production");
  }
  requireAcknowledgement(app, "productionAcknowledged");
  const monthlyBudgetUsd = Number(requiredString(app, "monthlyBudgetUsd"));
  if (!Number.isFinite(monthlyBudgetUsd) || monthlyBudgetUsd < 100) {
    throw new Error("monthlyBudgetUsd must be a number greater than or equal to 100");
  }
  const apiDomainName = requiredString(app, "apiDomainName").replace(/\.$/, "");
  const hostedZoneName = requiredString(app, "hostedZoneName").replace(/\.$/, "");
  const availabilityZones = requiredString(app, "availabilityZones")
    .split(",")
    .map((zone) => zone.trim())
    .filter(Boolean);
  if (availabilityZones.length !== 3 || new Set(availabilityZones).size !== 3) {
    throw new Error("availabilityZones must contain exactly three unique comma-separated zones");
  }
  if (apiDomainName !== hostedZoneName && !apiDomainName.endsWith(`.${hostedZoneName}`)) {
    throw new Error("apiDomainName must be inside hostedZoneName");
  }
  return {
    apiDomainName,
    availabilityZones,
    bedrockModelId: requiredString(app, "bedrockModelId"),
    budgetEmail: requiredString(app, "budgetEmail"),
    certificateArn: requiredString(app, "certificateArn"),
    hostedZoneId: requiredString(app, "hostedZoneId"),
    hostedZoneName,
    monthlyBudgetUsd,
    oidcAudience: requiredString(app, "oidcAudience"),
    oidcIssuer: requiredString(app, "oidcIssuer"),
    oidcJwksUrl: requiredString(app, "oidcJwksUrl"),
  };
}
