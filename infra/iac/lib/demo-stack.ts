import * as path from "node:path";
import {
  Annotations,
  Aspects,
  CfnOutput,
  CfnResource,
  Duration,
  IAspect,
  RemovalPolicy,
  Stack,
  StackProps,
} from "aws-cdk-lib";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as ecrassets from "aws-cdk-lib/aws-ecr-assets";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import { IConstruct } from "constructs";
import { createCostBudget } from "./budgets";
import { DemoConfig } from "./config";

const ALLOWED_DEMO_RESOURCES = new Set([
  "AWS::ApiGatewayV2::Api",
  "AWS::ApiGatewayV2::Integration",
  "AWS::ApiGatewayV2::Route",
  "AWS::ApiGatewayV2::Stage",
  "AWS::Budgets::Budget",
  "AWS::CDK::Metadata",
  "AWS::IAM::Policy",
  "AWS::IAM::Role",
  "AWS::Lambda::Function",
  "AWS::Lambda::Permission",
  "AWS::Logs::LogGroup",
]);

class FreeTierOnlyGuard implements IAspect {
  public visit(node: IConstruct): void {
    if (node instanceof CfnResource && !ALLOWED_DEMO_RESOURCES.has(node.cfnResourceType)) {
      Annotations.of(node).addError(
        `Demo stack rejects non-allowlisted resource ${node.cfnResourceType}`,
      );
    }
  }
}

export interface DemoStackProps extends StackProps {
  readonly config: DemoConfig;
}

export class DemoStack extends Stack {
  public constructor(scope: IConstruct, id: string, props: DemoStackProps) {
    super(scope, id, props);

    const logGroup = new logs.LogGroup(this, "ApiLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const apiFunction = new lambda.DockerImageFunction(this, "ApiFunction", {
      architecture: lambda.Architecture.X86_64,
      code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, "../../.."), {
        file: "infra/Dockerfile.lambda",
        platform: ecrassets.Platform.LINUX_AMD64,
      }),
      description: "Ephemeral deterministic supplier assurance demo",
      environment: {
        APP_ENV: "staging",
        AUTH_MODE: "dev",
        MODEL_BACKEND: "deterministic",
        REPOSITORY_BACKEND: "memory",
        CHECKPOINT_BACKEND: "memory",
        APPROVAL_WORKFLOW_BACKEND: "local",
      },
      logGroup,
      memorySize: 1024,
      timeout: Duration.seconds(30),
    });
    const httpApi = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: "supplier-assurance-demo",
      defaultIntegration: new integrations.HttpLambdaIntegration("LambdaIntegration", apiFunction),
      description: "Free-plan demo; synthetic data only and no persistence",
    });

    createCostBudget(this, "SupplierAssuranceDemoOneDollarAlarm", 1, props.config.budgetEmail);
    Aspects.of(this).add(new FreeTierOnlyGuard());

    new CfnOutput(this, "ApiUrl", { value: httpApi.apiEndpoint });
    new CfnOutput(this, "DemoDataPolicy", {
      value: "EPHEMERAL_SYNTHETIC_DATA_ONLY",
    });
    new CfnOutput(this, "ModelBackend", { value: "deterministic" });
  }
}
