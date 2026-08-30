import * as path from "node:path";
import {
  Arn,
  ArnFormat,
  CfnOutput,
  CfnResource,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  Validations,
} from "aws-cdk-lib";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecrassets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as rds from "aws-cdk-lib/aws-rds";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53Targets from "aws-cdk-lib/aws-route53-targets";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as sfnTasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { IConstruct } from "constructs";
import { createCostBudget } from "./budgets";
import { ProductionConfig } from "./config";

export interface ProductionStackProps extends StackProps {
  readonly config: ProductionConfig;
}

interface DatabaseEnvironment {
  readonly environment: Record<string, string>;
  readonly secrets: Record<string, ecs.Secret>;
}

export class ProductionStack extends Stack {
  public constructor(scope: IConstruct, id: string, props: ProductionStackProps) {
    super(scope, id, props);

    const key = new kms.Key(this, "DataKey", {
      alias: "alias/supplier-assurance-production",
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const evidenceBucket = new s3.Bucket(this, "EvidenceBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: key,
      enforceSSL: true,
      objectLockEnabled: true,
      objectLockDefaultRetention: s3.ObjectLockRetention.compliance(Duration.days(365)),
      removalPolicy: RemovalPolicy.RETAIN,
      versioned: true,
    });
    evidenceBucket.addLifecycleRule({
      noncurrentVersionExpiration: Duration.days(365),
      transitions: [{ storageClass: s3.StorageClass.INFREQUENT_ACCESS, transitionAfter: Duration.days(90) }],
    });

    const vpc = new ec2.Vpc(this, "Vpc", {
      ipAddresses: ec2.IpAddresses.cidr("10.42.0.0/16"),
      availabilityZones: props.config.availabilityZones,
      natGateways: 3,
      subnetConfiguration: [
        { cidrMask: 24, name: "public", subnetType: ec2.SubnetType.PUBLIC },
        { cidrMask: 24, name: "application", subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        { cidrMask: 24, name: "database", subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      ],
    });
    Validations.of(vpc).acknowledge({
      id: "CloudFormation-Validate::W3010",
      reason:
        "The account-specific AgentCore-supported Availability Zones are mandatory deployment input.",
    });
    vpc.addGatewayEndpoint("S3Endpoint", { service: ec2.GatewayVpcEndpointAwsService.S3 });

    const databaseSecurityGroup = new ec2.SecurityGroup(this, "DatabaseSecurityGroup", {
      allowAllOutbound: false,
      description: "PostgreSQL accepts traffic only from application workloads",
      vpc,
    });
    const database = new rds.DatabaseInstance(this, "Database", {
      allocatedStorage: 100,
      autoMinorVersionUpgrade: true,
      backupRetention: Duration.days(35),
      cloudwatchLogsExports: ["postgresql"],
      credentials: rds.Credentials.fromGeneratedSecret("supplier_admin", { encryptionKey: key }),
      databaseName: "supplier_assurance",
      deleteAutomatedBackups: false,
      deletionProtection: true,
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.R7G, ec2.InstanceSize.LARGE),
      maxAllocatedStorage: 1000,
      monitoringInterval: Duration.seconds(60),
      multiAz: true,
      performanceInsightEncryptionKey: key,
      performanceInsightRetention: rds.PerformanceInsightRetention.MONTHS_15,
      removalPolicy: RemovalPolicy.RETAIN,
      securityGroups: [databaseSecurityGroup],
      storageEncrypted: true,
      storageEncryptionKey: key,
      storageType: rds.StorageType.GP3,
      subnetGroup: new rds.SubnetGroup(this, "DatabaseSubnetGroup", {
        description: "Isolated database subnets",
        vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
        removalPolicy: RemovalPolicy.RETAIN,
      }),
      vpc,
    });

    const approvalStatusSecurityGroup = new ec2.SecurityGroup(
      this,
      "ApprovalStatusSecurityGroup",
      { vpc },
    );
    databaseSecurityGroup.addIngressRule(approvalStatusSecurityGroup, ec2.Port.tcp(5432));
    const approvalStatusFunction = new lambda.DockerImageFunction(
      this,
      "ApprovalStatusFunction",
      {
        architecture: lambda.Architecture.ARM_64,
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, "../../.."), {
          cmd: ["apps.approval_status.lambda_handler.handler"],
          file: "infra/Dockerfile.lambda",
          platform: ecrassets.Platform.LINUX_ARM64,
        }),
        description: "Read committed governed-action status for Step Functions",
        environment: {
          APP_ENV: "production",
          DATABASE_SECRET_ARN: database.secret?.secretArn ?? "",
        },
        logGroup: this.logGroup("ApprovalStatusLogs", key),
        memorySize: 1024,
        reservedConcurrentExecutions: 10,
        securityGroups: [approvalStatusSecurityGroup],
        timeout: Duration.seconds(30),
        vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      },
    );
    database.secret?.grantRead(approvalStatusFunction);
    key.grantDecrypt(approvalStatusFunction);

    const approved = new sfn.Succeed(this, "ApprovalSucceeded");
    const rejected = new sfn.Fail(this, "ApprovalRejected", {
      cause: "The governed supplier action was rejected by a human approver.",
      error: "SupplierActionRejected",
    });
    const unexpected = new sfn.Fail(this, "ApprovalUnexpectedState", {
      cause: "The governed supplier action entered an unexpected terminal state.",
      error: "SupplierActionUnexpectedState",
    });
    const waitForDecision = new sfn.Wait(this, "WaitForHumanDecision", {
      time: sfn.WaitTime.duration(Duration.minutes(5)),
    });
    const checkApproval = new sfnTasks.LambdaInvoke(this, "CheckCommittedApproval", {
      lambdaFunction: approvalStatusFunction,
      payload: sfn.TaskInput.fromObject({
        action_id: sfn.JsonPath.stringAt("$.action_id"),
      }),
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
    });
    checkApproval.addRetry({
      backoffRate: 2,
      errors: [
        "Lambda.ServiceException",
        "Lambda.AWSLambdaException",
        "Lambda.SdkClientException",
        "Lambda.TooManyRequestsException",
      ],
      interval: Duration.seconds(2),
      maxAttempts: 6,
    });
    const routeApproval = new sfn.Choice(this, "RouteApprovalStatus")
      .when(sfn.Condition.stringEquals("$.status", "pending_approval"), waitForDecision)
      .when(sfn.Condition.stringEquals("$.status", "approved"), approved)
      .when(sfn.Condition.stringEquals("$.status", "executed"), approved)
      .when(sfn.Condition.stringEquals("$.status", "rejected"), rejected)
      .otherwise(unexpected);
    checkApproval.next(routeApproval);
    waitForDecision.next(checkApproval);
    const approvalStateMachine = new sfn.StateMachine(this, "ApprovalStateMachine", {
      definitionBody: sfn.DefinitionBody.fromChainable(checkApproval),
      logs: {
        destination: new logs.LogGroup(this, "ApprovalWorkflowLogs", {
          encryptionKey: key,
          retention: logs.RetentionDays.THREE_MONTHS,
          removalPolicy: RemovalPolicy.RETAIN,
        }),
        level: sfn.LogLevel.ALL,
        includeExecutionData: false,
      },
      stateMachineName: "supplier-assurance-production-approvals",
      stateMachineType: sfn.StateMachineType.STANDARD,
      timeout: Duration.days(30),
      tracingEnabled: true,
    });

    const cluster = new ecs.Cluster(this, "Cluster", {
      containerInsightsV2: ecs.ContainerInsights.ENHANCED,
      enableFargateCapacityProviders: true,
      vpc,
    });
    cluster.addDefaultCloudMapNamespace({ name: "supplier.internal" });

    const apiImage = new ecrassets.DockerImageAsset(this, "ApiImage", {
      directory: path.join(__dirname, "../../.."),
      file: "infra/Dockerfile.api",
      platform: ecrassets.Platform.LINUX_ARM64,
    });
    const outboxImage = new ecrassets.DockerImageAsset(this, "OutboxImage", {
      directory: path.join(__dirname, "../../.."),
      file: "infra/Dockerfile.outbox",
      platform: ecrassets.Platform.LINUX_ARM64,
    });
    const mcpImage = new ecrassets.DockerImageAsset(this, "McpImage", {
      directory: path.join(__dirname, "../../.."),
      file: "infra/Dockerfile.mcp",
      platform: ecrassets.Platform.LINUX_ARM64,
    });
    const agentImage = new ecrassets.DockerImageAsset(this, "AgentCoreImage", {
      directory: path.join(__dirname, "../../.."),
      file: "infra/Dockerfile.agentcore",
      platform: ecrassets.Platform.LINUX_ARM64,
    });

    const appSecurityGroup = new ec2.SecurityGroup(this, "ApiSecurityGroup", { vpc });
    const outboxSecurityGroup = new ec2.SecurityGroup(this, "OutboxSecurityGroup", { vpc });
    const mcpSecurityGroup = new ec2.SecurityGroup(this, "McpSecurityGroup", { vpc });
    const agentSecurityGroup = new ec2.SecurityGroup(this, "AgentSecurityGroup", { vpc });
    databaseSecurityGroup.addIngressRule(appSecurityGroup, ec2.Port.tcp(5432));
    databaseSecurityGroup.addIngressRule(outboxSecurityGroup, ec2.Port.tcp(5432));
    databaseSecurityGroup.addIngressRule(mcpSecurityGroup, ec2.Port.tcp(5432));
    databaseSecurityGroup.addIngressRule(agentSecurityGroup, ec2.Port.tcp(5432));
    mcpSecurityGroup.addIngressRule(appSecurityGroup, ec2.Port.tcp(8001));

    const databaseEnvironment = this.databaseEnvironment(database);
    const apiTaskRole = new iam.Role(this, "ApiTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });
    this.grantBedrockInvoke(apiTaskRole);
    evidenceBucket.grantReadWrite(apiTaskRole);
    const apiTask = new ecs.FargateTaskDefinition(this, "ApiTask", {
      cpu: 1024,
      memoryLimitMiB: 2048,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
      taskRole: apiTaskRole,
    });
    const apiContainer = apiTask.addContainer("api", {
      image: ecs.ContainerImage.fromDockerImageAsset(apiImage),
      environment: {
        ...databaseEnvironment.environment,
        APP_ENV: "production",
        APPROVAL_WORKFLOW_BACKEND: "step_functions",
        AUTH_MODE: "oidc",
        AWS_REGION: this.region,
        BEDROCK_MODEL_ID: props.config.bedrockModelId,
        CHECKPOINT_BACKEND: "postgres",
        LANGGRAPH_STRICT_MSGPACK: "true",
        MODEL_BACKEND: "bedrock",
        OIDC_ALGORITHM: "RS256",
        OIDC_AUDIENCE: props.config.oidcAudience,
        OIDC_ISSUER: props.config.oidcIssuer,
        OIDC_JWKS_URL: props.config.oidcJwksUrl,
        REPOSITORY_BACKEND: "postgres",
        RUN_MIGRATIONS: "false",
        STEP_FUNCTIONS_STATE_MACHINE_ARN: approvalStateMachine.stateMachineArn,
      },
      healthCheck: {
        command: [
          "CMD-SHELL",
          "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\" || exit 1",
        ],
        interval: Duration.seconds(30),
        retries: 3,
        startPeriod: Duration.seconds(60),
        timeout: Duration.seconds(5),
      },
      logging: ecs.LogDrivers.awsLogs({
        logGroup: this.logGroup("ApiLogs", key),
        streamPrefix: "api",
      }),
      secrets: databaseEnvironment.secrets,
    });
    apiContainer.addPortMappings({ containerPort: 8000, name: "http" });

    const certificate = acm.Certificate.fromCertificateArn(
      this,
      "Certificate",
      props.config.certificateArn,
    );
    const apiService = new ecsPatterns.ApplicationLoadBalancedFargateService(
      this,
      "ApiService",
      {
        assignPublicIp: false,
        certificate,
        circuitBreaker: { rollback: true },
        cluster,
        desiredCount: 2,
        enableExecuteCommand: true,
        listenerPort: 443,
        maxHealthyPercent: 200,
        minHealthyPercent: 100,
        platformVersion: ecs.FargatePlatformVersion.LATEST,
        publicLoadBalancer: true,
        redirectHTTP: true,
        securityGroups: [appSecurityGroup],
        sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS,
        taskDefinition: apiTask,
        taskSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      },
    );
    apiService.targetGroup.configureHealthCheck({
      healthyHttpCodes: "200",
      interval: Duration.seconds(30),
      path: "/health",
      timeout: Duration.seconds(5),
    });
    const scaling = apiService.service.autoScaleTaskCount({ minCapacity: 2, maxCapacity: 10 });
    scaling.scaleOnCpuUtilization("CpuScaling", { targetUtilizationPercent: 60 });
    const hostedZone = route53.HostedZone.fromHostedZoneAttributes(this, "HostedZone", {
      hostedZoneId: props.config.hostedZoneId,
      zoneName: props.config.hostedZoneName,
    });
    new route53.ARecord(this, "ApiAliasRecord", {
      recordName: props.config.apiDomainName,
      target: route53.RecordTarget.fromAlias(
        new route53Targets.LoadBalancerTarget(apiService.loadBalancer),
      ),
      zone: hostedZone,
    });

    const outboxRole = new iam.Role(this, "OutboxTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });
    approvalStateMachine.grantStartExecution(outboxRole);
    const outboxTask = this.workerTask(
      "OutboxTask",
      "outbox-worker",
      outboxImage,
      outboxRole,
      {
        ...databaseEnvironment.environment,
        APP_ENV: "production",
        APPROVAL_WORKFLOW_BACKEND: "step_functions",
        AWS_REGION: this.region,
        REPOSITORY_BACKEND: "postgres",
        STEP_FUNCTIONS_STATE_MACHINE_ARN: approvalStateMachine.stateMachineArn,
      },
      databaseEnvironment.secrets,
      key,
    );
    new ecs.FargateService(this, "OutboxService", {
      assignPublicIp: false,
      circuitBreaker: { rollback: true },
      cluster,
      desiredCount: 2,
      enableExecuteCommand: true,
      maxHealthyPercent: 200,
      minHealthyPercent: 100,
      securityGroups: [outboxSecurityGroup],
      taskDefinition: outboxTask,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    const mcpRole = new iam.Role(this, "McpTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });
    const mcpTask = this.workerTask(
      "McpTask",
      "mcp-server",
      mcpImage,
      mcpRole,
      {
        ...databaseEnvironment.environment,
        APP_ENV: "production",
        MCP_HOST: "0.0.0.0",
        MCP_PORT: "8001",
        REPOSITORY_BACKEND: "postgres",
      },
      databaseEnvironment.secrets,
      key,
      8001,
    );
    new ecs.FargateService(this, "McpService", {
      assignPublicIp: false,
      circuitBreaker: { rollback: true },
      cloudMapOptions: { name: "mcp" },
      cluster,
      desiredCount: 2,
      enableExecuteCommand: true,
      maxHealthyPercent: 200,
      minHealthyPercent: 100,
      securityGroups: [mcpSecurityGroup],
      taskDefinition: mcpTask,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    const agentRole = new iam.Role(this, "AgentCoreRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com").withConditions({
        ArnLike: {
          "aws:SourceArn": `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:*`,
        },
        StringEquals: { "aws:SourceAccount": this.account },
      }),
    });
    this.grantBedrockInvoke(agentRole);
    database.secret?.grantRead(agentRole);
    key.grantDecrypt(agentRole);
    agentImage.repository.grantPull(agentRole);
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "logs:CreateLogGroup",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:PutResourcePolicy",
        ],
        resources: [
          `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
          `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
        ],
      }),
    );
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ],
        resources: ["*"],
      }),
    );
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["cloudwatch:PutMetricData"],
        conditions: { StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" } },
        resources: ["*"],
      }),
    );
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:GetWorkloadAccessToken"],
        resources: [
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/SupplierAssuranceProduction-*`,
        ],
      }),
    );
    const agentRuntime = new CfnResource(this, "AgentCoreRuntime", {
      type: "AWS::BedrockAgentCore::Runtime",
      properties: {
        AgentRuntimeArtifact: {
          ContainerConfiguration: { ContainerUri: agentImage.imageUri },
        },
        AgentRuntimeName: "SupplierAssuranceProduction",
        Description: "Production LangGraph supplier review runtime",
        EnvironmentVariables: {
          APP_ENV: "production",
          APPROVAL_WORKFLOW_BACKEND: "local",
          AWS_REGION: this.region,
          BEDROCK_MODEL_ID: props.config.bedrockModelId,
          CHECKPOINT_BACKEND: "postgres",
          DATABASE_SECRET_ARN: database.secret?.secretArn ?? "",
          LANGGRAPH_STRICT_MSGPACK: "true",
          MODEL_BACKEND: "bedrock",
          REPOSITORY_BACKEND: "postgres",
        },
        LifecycleConfiguration: {
          IdleRuntimeSessionTimeout: 900,
          MaxLifetime: 28_800,
        },
        NetworkConfiguration: {
          NetworkMode: "VPC",
          NetworkModeConfig: {
            SecurityGroups: [agentSecurityGroup.securityGroupId],
            Subnets: vpc.selectSubnets({
              subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
            }).subnetIds,
          },
        },
        ProtocolConfiguration: "HTTP",
        RoleArn: agentRole.roleArn,
        Tags: { Environment: "production", Workload: "supplier-assurance" },
      },
    });
    agentRuntime.node.addDependency(agentImage);
    const runtimeEndpoint = new CfnResource(this, "AgentCoreRuntimeEndpoint", {
      type: "AWS::BedrockAgentCore::RuntimeEndpoint",
      properties: {
        AgentRuntimeId: agentRuntime.getAtt("AgentRuntimeId"),
        AgentRuntimeVersion: agentRuntime.getAtt("AgentRuntimeVersion"),
        Description: "Stable production endpoint",
        Name: "Production",
        Tags: { Environment: "production" },
      },
    });
    runtimeEndpoint.addResourceDependency(agentRuntime);

    const webAcl = new wafv2.CfnWebACL(this, "WebAcl", {
      defaultAction: { allow: {} },
      scope: "REGIONAL",
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: "supplier-assurance-production-waf",
        sampledRequestsEnabled: true,
      },
      rules: [
        this.managedWafRule("AWSManagedRulesCommonRuleSet", 10),
        this.managedWafRule("AWSManagedRulesKnownBadInputsRuleSet", 20),
      ],
    });
    new wafv2.CfnWebACLAssociation(this, "WebAclAssociation", {
      resourceArn: apiService.loadBalancer.loadBalancerArn,
      webAclArn: webAcl.attrArn,
    });

    apiService.service.metricCpuUtilization().createAlarm(this, "ApiCpuAlarm", {
      evaluationPeriods: 3,
      threshold: 80,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    });
    database.metricCPUUtilization().createAlarm(this, "DatabaseCpuAlarm", {
      evaluationPeriods: 3,
      threshold: 80,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    });
    apiService.loadBalancer.metrics.httpCodeElb(
      elbv2.HttpCodeElb.ELB_5XX_COUNT,
    ).createAlarm(this, "LoadBalancerFiveHundredAlarm", {
      evaluationPeriods: 1,
      threshold: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    });
    createCostBudget(
      this,
      "SupplierAssuranceProductionBudget",
      props.config.monthlyBudgetUsd,
      props.config.budgetEmail,
    );

    new CfnOutput(this, "ApiUrl", {
      value: `https://${props.config.apiDomainName}`,
    });
    new CfnOutput(this, "AgentRuntimeArn", { value: agentRuntime.ref });
    new CfnOutput(this, "AgentRuntimeEndpointArn", { value: runtimeEndpoint.ref });
    new CfnOutput(this, "ApprovalStateMachineArn", {
      value: approvalStateMachine.stateMachineArn,
    });
    new CfnOutput(this, "ClusterName", { value: cluster.clusterName });
    new CfnOutput(this, "DatabaseSecretArn", { value: database.secret?.secretArn ?? "" });
    new CfnOutput(this, "EvidenceBucketName", { value: evidenceBucket.bucketName });
    new CfnOutput(this, "MigrationSecurityGroup", {
      value: appSecurityGroup.securityGroupId,
    });
    new CfnOutput(this, "MigrationSubnets", {
      value: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds.join(","),
    });
    new CfnOutput(this, "MigrationTaskDefinition", { value: apiTask.taskDefinitionArn });
  }

  private databaseEnvironment(database: rds.DatabaseInstance): DatabaseEnvironment {
    if (!database.secret) {
      throw new Error("The production database must have a generated secret");
    }
    return {
      environment: {
        DATABASE_HOST: database.dbInstanceEndpointAddress,
        DATABASE_NAME: "supplier_assurance",
        DATABASE_PORT: database.dbInstanceEndpointPort,
        DATABASE_USER: "supplier_admin",
      },
      secrets: { DATABASE_PASSWORD: ecs.Secret.fromSecretsManager(database.secret, "password") },
    };
  }

  private workerTask(
    id: string,
    name: string,
    image: ecrassets.DockerImageAsset,
    taskRole: iam.IRole,
    environment: Record<string, string>,
    secrets: Record<string, ecs.Secret>,
    key: kms.IKey,
    port?: number,
  ): ecs.FargateTaskDefinition {
    const task = new ecs.FargateTaskDefinition(this, id, {
      cpu: 512,
      memoryLimitMiB: 1024,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
      taskRole,
    });
    const container = task.addContainer(name, {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      environment,
      logging: ecs.LogDrivers.awsLogs({
        logGroup: this.logGroup(`${id}Logs`, key),
        streamPrefix: name,
      }),
      secrets,
    });
    if (port !== undefined) {
      container.addPortMappings({ containerPort: port, name: "mcp" });
    }
    return task;
  }

  private logGroup(id: string, key: kms.IKey): logs.LogGroup {
    return new logs.LogGroup(this, id, {
      encryptionKey: key,
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: RemovalPolicy.RETAIN,
    });
  }

  private grantBedrockInvoke(grantee: iam.IGrantable): void {
    const foundationModels = Arn.format(
      {
        arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
        service: "bedrock",
        region: "*",
        account: "",
        resource: "foundation-model",
        resourceName: "*",
      },
      this,
    );
    const inferenceProfiles = Arn.format(
      {
        arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
        service: "bedrock",
        region: "*",
        resource: "inference-profile",
        resourceName: "*",
      },
      this,
    );
    iam.Grant.addToPrincipal({
      grantee,
      actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      resourceArns: [foundationModels, inferenceProfiles],
    });
  }

  private managedWafRule(name: string, priority: number): wafv2.CfnWebACL.RuleProperty {
    return {
      name,
      overrideAction: { none: {} },
      priority,
      statement: {
        managedRuleGroupStatement: { name, vendorName: "AWS" },
      },
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: name,
        sampledRequestsEnabled: true,
      },
    };
  }
}
