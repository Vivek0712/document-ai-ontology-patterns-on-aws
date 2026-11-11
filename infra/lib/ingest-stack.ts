/**
 * rp-ingest: B's stage Lambdas, the docling queue (+DLQ), ECS cluster and the docling-serve Fargate
 * service (upstream CPU image, Cloud Map docling.rp.local, exactly one task, scale to zero: ExtractOpen
 * raises the floor on demand, a 5-minute reaper lowers it, SQS alarms are the backstop),
 * the `rp-ingest` state machine, the EventBridge triggers (two executions per Bronze object, one per
 * pipeline version) and the DocStatus stream -> package rollup.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as appscaling from 'aws-cdk-lib/aws-applicationautoscaling';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as servicediscovery from 'aws-cdk-lib/aws-servicediscovery';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { addAossDataPolicy } from './aoss';
import { BaseResources } from './base-stack';
import { buildIngestAsl } from './ingest-asl';
import { PIPELINE_VERSIONS, SSM } from './names';
import { buildFunctions } from './rp-functions';

export interface IngestStackProps extends StackProps {
  base: BaseResources;
  aossEndpoint: string;
  aossCollectionArn: string;
  neptuneEndpoint: string;
  neptuneResourceId: string;
  doclingImage: string;
}

export const STATE_MACHINE_NAME = 'rp-ingest';
export const DOCLING_PORT = 5001;
/** docling-serve keeps async task state in process memory (engine `local`), so exactly one task. */
export const DOCLING_MAX_TASKS = 1;
export const DOCLING_FLOOR_PARAM = '/rp/docling/floor';

export class IngestStack extends Stack {
  readonly stateMachine: sfn.StateMachine;
  readonly doclingQueue: sqs.Queue;

  constructor(scope: Construct, id: string, props: IngestStackProps) {
    super(scope, id, props);
    const { base } = props;

    // --- docling queue -----------------------------------------------------------------------------
    const dlq = new sqs.Queue(this, 'DoclingDlq', {
      queueName: 'rp-docling-dlq',
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: Duration.days(14),
      enforceSSL: true,
    });
    this.doclingQueue = new sqs.Queue(this, 'DoclingQueue', {
      queueName: 'rp-docling',
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      visibilityTimeout: Duration.seconds(960), // > rp-docling-call timeout (900 s)
      retentionPeriod: Duration.days(1),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 10 },
      enforceSSL: true,
    });

    // --- docling-serve on Fargate ---------------------------------------------------------------------
    const namespace = new servicediscovery.PrivateDnsNamespace(this, 'Namespace', {
      name: 'rp.local',
      vpc: base.vpc,
      description: 'Regionproof private service names',
    });
    const cluster = new ecs.Cluster(this, 'Cluster', { clusterName: 'rp-cluster', vpc: base.vpc });
    const taskDef = new ecs.FargateTaskDefinition(this, 'DoclingTask', {
      family: 'rp-docling',
      cpu: 4096,
      memoryLimitMiB: 16384,
      ephemeralStorageGiB: 40,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });
    const doclingLogs = new logs.LogGroup(this, 'DoclingLogs', {
      logGroupName: '/rp/ecs/docling',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    taskDef.addContainer('docling', {
      containerName: 'docling-serve',
      image: ecs.ContainerImage.fromRegistry(props.doclingImage),
      portMappings: [{ containerPort: DOCLING_PORT, protocol: ecs.Protocol.TCP }],
      environment: {
        DOCLING_SERVE_ENABLE_UI: 'false',
        DOCLING_SERVE_MAX_SYNC_WAIT: '600',
        // Async task state lives in this process (the `local` engine): the service runs exactly one task,
        // so submit, poll and result always reach the same instance. Parallelism comes from workers.
        DOCLING_SERVE_ENG_KIND: 'local',
        DOCLING_SERVE_ENG_LOC_NUM_WORKERS: '2',
        UVICORN_PORT: String(DOCLING_PORT),
      },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'docling', logGroup: doclingLogs }),
      healthCheck: {
        command: ['CMD-SHELL',
          `python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:${DOCLING_PORT}/health', timeout=4)" || exit 1`],
        interval: Duration.seconds(15),
        timeout: Duration.seconds(5),
        retries: 4,
        startPeriod: Duration.seconds(180),
      },
    });
    const doclingSg = new ec2.SecurityGroup(this, 'DoclingSg', {
      vpc: base.vpc,
      securityGroupName: 'rp-docling-sg',
      description: 'docling-serve: 5001 from rp-lambda-sg; egress for the image pull',
      allowAllOutbound: true,
    });
    doclingSg.addIngressRule(base.lambdaSg, ec2.Port.tcp(DOCLING_PORT), 'rp-docling-call');
    const service = new ecs.FargateService(this, 'DoclingService', {
      serviceName: 'rp-docling',
      cluster,
      taskDefinition: taskDef,
      desiredCount: 0, // ExtractOpen (demand) or scripts/prewarm.sh sets min 1 on the scalable target
      minHealthyPercent: 0,
      maxHealthyPercent: 100, // never two tasks, not even during a deployment (task state is in memory)
      assignPublicIp: true, // public subnets, no NAT: needed to pull from quay.io
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [doclingSg],
      cloudMapOptions: {
        name: 'docling',
        cloudMapNamespace: namespace,
        dnsRecordType: servicediscovery.DnsRecordType.A,
        dnsTtl: Duration.seconds(10),
      },
      circuitBreaker: { enable: true, rollback: false },
      enableExecuteCommand: false,
    });
    this.addDoclingScaling(cluster, service);

    // --- stage Lambdas ----------------------------------------------------------------------------------
    const fns = buildFunctions(this, 'ingest', base, {
      aossEndpoint: props.aossEndpoint,
      aossCollectionArn: props.aossCollectionArn,
      neptuneEndpoint: props.neptuneEndpoint,
      neptuneResourceId: props.neptuneResourceId,
      doclingUrl: `http://docling.rp.local:${DOCLING_PORT}`,
      doclingQueueUrl: this.doclingQueue.queueUrl,
    });
    addAossDataPolicy(this, 'rp-units-ingest', fns.aossPrincipals);

    const doclingCall = fns.get('rp-docling-call');
    // rp-docling-call returns batchItemFailures (a not-yet-healthy docling-serve leaves the message for a retry).
    doclingCall.addEventSource(new sources.SqsEventSource(this.doclingQueue, { batchSize: 1, reportBatchItemFailures: true }));

    this.addDoclingDemandScaling(fns.get('rp-stage-status'), service);

    fns.get('rp-status-stream').addEventSource(new sources.DynamoEventSource(base.tables.doc_status, {
      startingPosition: lambda.StartingPosition.LATEST,
      batchSize: 100,
      bisectBatchOnError: true,
      retryAttempts: 5,
      filters: [lambda.FilterCriteria.filter({
        dynamodb: { NewImage: { recordType: { S: lambda.FilterRule.or('STAGE', 'LANE') } } },
      })],
    }));

    // --- state machine ------------------------------------------------------------------------------------
    const arn = (name: string): string => fns.get(name).functionArn;
    const asl = buildIngestAsl({
      fn: {
        register: arn('rp-register'),
        textLayer: arn('rp-text-layer'),
        naiveChunk: arn('rp-naive-chunk'),
        route: arn('rp-route'),
        stageStatus: arn('rp-stage-status'),
        textractPage: arn('rp-textract-page'),
        classify: arn('rp-classify'),
        euBuilder: arn('rp-eu-builder'),
        piiGate: arn('rp-pii-gate'),
        promote: arn('rp-promote'),
        embedIndex: arn('rp-embed-index'),
        graphWrite: arn('rp-graph-write'),
        markReady: arn('rp-mark-ready'),
      },
      doclingQueueUrl: this.doclingQueue.queueUrl,
    });
    const sfnLogs = new logs.LogGroup(this, 'IngestLogs', {
      logGroupName: '/aws/vendedlogs/states/rp-ingest',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.stateMachine = new sfn.StateMachine(this, 'Ingest', {
      stateMachineName: STATE_MACHINE_NAME,
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromString(this.toJsonString(asl)),
      tracingEnabled: true,
      logs: { destination: sfnLogs, level: sfn.LogLevel.ALL, includeExecutionData: false },
      timeout: Duration.hours(1),
    });
    for (const name of ['rp-register', 'rp-text-layer', 'rp-naive-chunk', 'rp-route', 'rp-stage-status',
      'rp-textract-page', 'rp-classify', 'rp-eu-builder', 'rp-pii-gate', 'rp-promote', 'rp-embed-index',
      'rp-graph-write', 'rp-mark-ready']) {
      fns.get(name).grantInvoke(this.stateMachine);
    }
    this.doclingQueue.grantSendMessages(this.stateMachine);

    // --- triggers ------------------------------------------------------------------------------------------
    const created = new events.Rule(this, 'BronzeCreated', {
      ruleName: 'rp-bronze-created',
      description: 'Bronze upload -> one rp-ingest execution per pipeline version',
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: { bucket: { name: [base.bucket.bucketName] }, object: { key: [{ prefix: 'bronze/' }] } },
      },
    });
    for (const pv of PIPELINE_VERSIONS) {
      created.addTarget(new targets.SfnStateMachine(this.stateMachine, {
        input: events.RuleTargetInput.fromObject({
          pipelineVersion: pv,
          bucket: events.EventField.fromPath('$.detail.bucket.name'),
          key: events.EventField.fromPath('$.detail.object.key'),
          versionId: events.EventField.fromPath('$.detail.object.version-id'),
          etag: events.EventField.fromPath('$.detail.object.etag'),
          size: events.EventField.fromPath('$.detail.object.size'),
          eventTime: events.EventField.time,
          trigger: 's3',
        }),
        retryAttempts: 3,
      }));
    }
    new events.Rule(this, 'IngestFailed', {
      ruleName: 'rp-ingest-failed',
      description: 'Backstop: mark the lane FAILED when an execution fails without reaching LaneFailed',
      eventPattern: {
        source: ['aws.states'],
        detailType: ['Step Functions Execution Status Change'],
        detail: { status: ['FAILED', 'TIMED_OUT', 'ABORTED'], stateMachineArn: [this.stateMachine.stateMachineArn] },
      },
      targets: [new targets.LambdaFunction(fns.get('rp-stage-status'), {
        event: events.RuleTargetInput.fromObject({
          op: 'execution_event',
          detail: events.EventField.fromPath('$.detail'),
        }),
        retryAttempts: 3,
      })],
    });

    new ssm.StringParameter(this, 'StateMachineArnParam', {
      parameterName: SSM.stateMachineArn, stringValue: this.stateMachine.stateMachineArn,
    });
    new ssm.StringParameter(this, 'DoclingQueueUrlParam', {
      parameterName: SSM.doclingQueueUrl, stringValue: this.doclingQueue.queueUrl,
    });
    new CfnOutput(this, 'StateMachineArn', { value: this.stateMachine.stateMachineArn });
    new CfnOutput(this, 'DoclingQueueUrl', { value: this.doclingQueue.queueUrl });
  }

  /**
   * Demand-driven floor (services/lambdas/ingest_lib/docling_scaler.py): rp-stage-status raises the
   * scaling floor to 1 at ExtractOpen and lowers it from a 5-minute schedule once the queue is empty and
   * the last demand is 20 minutes old. The floor owner (none | demand | prewarm) is an SSM parameter
   * that prewarm.sh and pause.sh also write.
   */
  private addDoclingDemandScaling(fn: lambda.Function, service: ecs.FargateService): void {
    const floor = new ssm.StringParameter(this, 'DoclingFloorParam', {
      parameterName: DOCLING_FLOOR_PARAM,
      stringValue: JSON.stringify({ owner: 'none', at: 0 }),
      description: 'docling-serve scaling floor owner: none | demand | prewarm (docling_scaler.py)',
    });
    floor.grantRead(fn);
    floor.grantWrite(fn);
    this.doclingQueue.grant(fn, 'sqs:GetQueueAttributes');
    fn.addEnvironment('RP_DOCLING_QUEUE_URL', this.doclingQueue.queueUrl);
    fn.addEnvironment('RP_DOCLING_FLOOR_PARAM', DOCLING_FLOOR_PARAM);
    fn.addEnvironment('RP_DOCLING_CLUSTER', 'rp-cluster');
    fn.addEnvironment('RP_DOCLING_SERVICE', 'rp-docling');
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['application-autoscaling:DescribeScalableTargets', 'application-autoscaling:RegisterScalableTarget'],
      resources: ['*'],
    }));
    // RegisterScalableTarget for an ECS service also checks these on the caller.
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ecs:DescribeServices', 'ecs:UpdateService'],
      resources: [service.serviceArn],
    }));
    new events.Rule(this, 'DoclingReaper', {
      ruleName: 'rp-docling-reaper',
      description: 'Lower the docling-serve scaling floor after 20 idle minutes with an empty queue',
      schedule: events.Schedule.rate(Duration.minutes(5)),
      targets: [new targets.LambdaFunction(fn, {
        event: events.RuleTargetInput.fromObject({ op: 'docling_reap' }),
        retryAttempts: 1,
      })],
    });
  }

  /**
   * Backstop step scaling on SQS visible + in-flight (the Lambda event source keeps messages in flight):
   * scale out within a minute of a message, scale in to zero after 15 idle minutes. Max capacity is 1:
   * docling-serve's async task state is per process. The floor is 0 unless demand or prewarm raised it.
   */
  private addDoclingScaling(cluster: ecs.Cluster, service: ecs.FargateService): void {
    const target = new appscaling.ScalableTarget(this, 'DoclingScalableTarget', {
      serviceNamespace: appscaling.ServiceNamespace.ECS,
      resourceId: `service/${cluster.clusterName}/${service.serviceName}`,
      scalableDimension: 'ecs:service:DesiredCount',
      minCapacity: 0,
      maxCapacity: DOCLING_MAX_TASKS,
    });
    target.node.addDependency(service);
    const period = Duration.minutes(1);
    const depth = new cloudwatch.MathExpression({
      label: 'docling visible + in-flight',
      expression: 'visible + inflight',
      usingMetrics: {
        visible: this.doclingQueue.metricApproximateNumberOfMessagesVisible({ period, statistic: 'Maximum' }),
        inflight: this.doclingQueue.metricApproximateNumberOfMessagesNotVisible({ period, statistic: 'Maximum' }),
      },
      period,
    });

    const scaleOut = new appscaling.StepScalingAction(this, 'DoclingScaleOut', {
      scalingTarget: target,
      adjustmentType: appscaling.AdjustmentType.EXACT_CAPACITY,
      cooldown: Duration.seconds(60),
      metricAggregationType: appscaling.MetricAggregationType.MAXIMUM,
    });
    scaleOut.addAdjustment({ lowerBound: 0, adjustment: 1 });
    new cloudwatch.Alarm(this, 'DoclingQueueBusy', {
      alarmName: 'rp-docling-queue-busy',
      metric: depth,
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cwActions.ApplicationScalingAction(scaleOut));

    const scaleIn = new appscaling.StepScalingAction(this, 'DoclingScaleIn', {
      scalingTarget: target,
      adjustmentType: appscaling.AdjustmentType.EXACT_CAPACITY,
      cooldown: Duration.seconds(300),
      metricAggregationType: appscaling.MetricAggregationType.MAXIMUM,
    });
    scaleIn.addAdjustment({ upperBound: 0, adjustment: 0 });
    new cloudwatch.Alarm(this, 'DoclingQueueIdle', {
      alarmName: 'rp-docling-queue-idle',
      metric: depth,
      threshold: 0,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
      evaluationPeriods: 15,
      treatMissingData: cloudwatch.TreatMissingData.BREACHING,
    }).addAlarmAction(new cwActions.ApplicationScalingAction(scaleIn));
  }
}
