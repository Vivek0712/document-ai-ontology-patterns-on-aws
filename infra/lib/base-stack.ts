/**
 * rp-base: KMS key, data bucket (versioned, SSE-KMS, EventBridge notifications), the DynamoDB tables
 * from the contract schema, the VPC (no NAT; S3 and DynamoDB gateway endpoints; one Step Functions
 * interface endpoint for the docling shim), Lambda layers, and the config deployment
 * (config/, prompts/ -> config/prompts/, ontology/dist/ -> config/ontology/).
 */
import * as fs from 'fs';
import * as path from 'path';
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { BuildAssets, LayerName } from './build-assets';
import { REPO_ROOT, SSM, TableLogical, TableSpec, loadTableSpecs } from './names';

export interface BaseStackProps extends StackProps {
  assets: BuildAssets;
  repoRoot?: string;
  /** Explicit AZs keep synth free of context lookups (no AWS call at synth time). */
  availabilityZones: string[];
}

/** What later stacks consume. */
export interface BaseResources {
  key: kms.IKey;
  bucket: s3.Bucket;
  tables: Record<TableLogical, dynamodb.Table>;
  vpc: ec2.Vpc;
  lambdaSg: ec2.SecurityGroup;
  layers: Partial<Record<LayerName, lambda.ILayerVersion>>;
  assets: BuildAssets;
}

export class BaseStack extends Stack implements BaseResources {
  readonly key: kms.Key;
  readonly bucket: s3.Bucket;
  readonly tables: Record<TableLogical, dynamodb.Table>;
  readonly vpc: ec2.Vpc;
  readonly lambdaSg: ec2.SecurityGroup;
  readonly layers: Partial<Record<LayerName, lambda.ILayerVersion>> = {};
  readonly assets: BuildAssets;
  private readonly azs: string[];

  constructor(scope: Construct, id: string, props: BaseStackProps) {
    super(scope, id, props);
    this.azs = props.availabilityZones;
    this.assets = props.assets;
    const repoRoot = props.repoRoot ?? REPO_ROOT;

    this.key = new kms.Key(this, 'Key', {
      alias: 'alias/rp-data',
      description: 'Regionproof data: S3 tiers and DynamoDB tables',
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    this.bucket = new s3.Bucket(this, 'DataBucket', {
      bucketName: `rp-data-${this.account}-${this.region}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.key,
      bucketKeyEnabled: true,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      eventBridgeEnabled: true,
      removalPolicy: RemovalPolicy.RETAIN,
      lifecycleRules: [
        { id: 'expire-noncurrent', noncurrentVersionExpiration: Duration.days(30) },
        { id: 'abort-mpu', abortIncompleteMultipartUploadAfter: Duration.days(1) },
      ],
      // Browser CORS for presigned PUT/GET is set by rp-web (it knows the CloudFront origin).
    });

    const specs = loadTableSpecs(repoRoot);
    this.tables = {} as Record<TableLogical, dynamodb.Table>;
    for (const [logical, spec] of Object.entries(specs) as [TableLogical, TableSpec][]) {
      this.tables[logical] = this.table(logical, spec);
    }

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: 'rp-vpc',
      availabilityZones: props.availabilityZones,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'private', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
      gatewayEndpoints: {
        S3: { service: ec2.GatewayVpcEndpointAwsService.S3 },
        DynamoDB: { service: ec2.GatewayVpcEndpointAwsService.DYNAMODB },
      },
    });
    this.lambdaSg = new ec2.SecurityGroup(this, 'LambdaSg', {
      vpc: this.vpc,
      securityGroupName: 'rp-lambda-sg',
      description: 'VPC Lambdas (docling shim, graph_*, neptune_load, vocab_export)',
      allowAllOutbound: true,
    });
    const endpointSg = new ec2.SecurityGroup(this, 'EndpointSg', {
      vpc: this.vpc,
      securityGroupName: 'rp-endpoint-sg',
      description: 'Interface endpoints: HTTPS from rp-lambda-sg',
      allowAllOutbound: false,
    });
    endpointSg.addIngressRule(this.lambdaSg, ec2.Port.tcp(443), 'VPC Lambdas');
    this.vpc.addInterfaceEndpoint('StatesEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [endpointSg],
      privateDnsEnabled: true,
    });

    for (const name of ['core', 'ingest', 'agent', 'api'] as LayerName[]) {
      const dir = props.assets.layerDir(name);
      if (!dir) continue;
      this.layers[name] = new lambda.LayerVersion(this, `Layer${name[0].toUpperCase()}${name.slice(1)}`, {
        layerVersionName: `rp-${name}`,
        description: `Regionproof ${name} dependencies (scripts/build_lambdas.sh)`,
        code: lambda.Code.fromAsset(dir),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
        compatibleArchitectures: [lambda.Architecture.X86_64],
        removalPolicy: RemovalPolicy.RETAIN,
      });
    }

    this.deployConfig(repoRoot);

    const param = (id: string, name: string, value: string): void => {
      new ssm.StringParameter(this, id, { parameterName: name, stringValue: value });
    };
    param('DataBucketParam', SSM.dataBucket, this.bucket.bucketName);
    param('LambdaSgParam', SSM.lambdaSgId, this.lambdaSg.securityGroupId);
    param('PrivateSubnetsParam', SSM.privateSubnetIds,
      this.vpc.isolatedSubnets.map((s) => s.subnetId).join(','));
    new CfnOutput(this, 'DataBucketName', { value: this.bucket.bucketName });
  }

  /** Fixed AZs: no `availability-zones` context lookup (synth needs no AWS credentials). */
  get availabilityZones(): string[] {
    return this.azs;
  }

  private table(logical: TableLogical, spec: TableSpec): dynamodb.Table {
    const attr = (name: string): dynamodb.Attribute => ({ name, type: dynamodb.AttributeType.STRING });
    const id = logical.replace(/(^|_)(\w)/g, (_m, _u, c: string) => c.toUpperCase()) + 'Table';
    const table = new dynamodb.Table(this, id, {
      tableName: spec.name,
      partitionKey: attr(spec.pk),
      sortKey: spec.sk ? attr(spec.sk) : undefined,
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: this.key,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      timeToLiveAttribute: spec.ttl ?? undefined,
      stream: spec.stream ? dynamodb.StreamViewType.NEW_IMAGE : undefined,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    for (const [indexName, pk, sk] of spec.gsis) {
      table.addGlobalSecondaryIndex({
        indexName,
        partitionKey: attr(pk),
        sortKey: sk ? attr(sk) : undefined,
        projectionType: dynamodb.ProjectionType.ALL,
      });
    }
    return table;
  }

  /**
   * config/ (pipelines, models, prices), prompts/ -> config/prompts/, ontology/dist/ -> config/ontology/, and the golden
   * set eval/golden.jsonl -> config/golden.jsonl (W-16 reads it through GET /artifacts).
   */
  private deployConfig(repoRoot: string): void {
    const deploy = (id: string, dir: string, prefix: string, exclude = ['.*', '**/__pycache__/**']): void => {
      if (!fs.existsSync(dir) || fs.readdirSync(dir).filter((f) => !f.startsWith('.')).length === 0) return;
      new s3deploy.BucketDeployment(this, id, {
        destinationBucket: this.bucket,
        destinationKeyPrefix: prefix,
        sources: [s3deploy.Source.asset(dir, { exclude })],
        prune: false,
        retainOnDelete: true,
        memoryLimit: 512,
      });
    };
    deploy('ConfigDeploy', path.join(repoRoot, 'config'), 'config/');
    deploy('PromptsDeploy', path.join(repoRoot, 'prompts'), 'config/prompts/');
    deploy('OntologyDeploy', path.join(repoRoot, 'ontology', 'dist'), 'config/ontology/');
    deploy('GoldenDeploy', path.join(repoRoot, 'eval'), 'config/', ['*', '!golden.jsonl']);
  }
}
