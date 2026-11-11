/**
 * rp-knowledge: OpenSearch Serverless vector collection `rp-units` in the NextGen collection group
 * `rp-units-group` (min 0 OCU; encryption, network and data access policies) and Neptune Database Serverless `rp-graph` (IAM auth on, private isolated subnets),
 * plus C's VPC admin functions (neptune_load, graph_admin, vocab_export).
 */
import { CfnOutput, CfnResource, Fn, Stack, StackProps } from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as neptune from 'aws-cdk-lib/aws-neptune';
import * as aoss from 'aws-cdk-lib/aws-opensearchserverless';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { AOSS_COLLECTION, addAossDataPolicy } from './aoss';
import { BaseResources } from './base-stack';
import { SSM } from './names';

export const AOSS_COLLECTION_GROUP = 'rp-units-group';
/** Upper OCU bound per dimension (indexing, search) for the NextGen group: the cost ceiling. */
export const AOSS_MAX_OCU = 4;
import { buildFunctions } from './rp-functions';

export interface KnowledgeStackProps extends StackProps {
  base: BaseResources;
  neptuneEngineVersion: string;
  /** Extra IAM principals (operator role) allowed to manage the AOSS indexes, e.g. for reset.sh. */
  aossAdminArns?: string[];
}

export class KnowledgeStack extends Stack {
  readonly aossEndpoint: string;
  readonly aossCollectionArn: string;
  readonly neptuneEndpoint: string;
  readonly neptuneResourceId: string;
  readonly neptuneSg: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: KnowledgeStackProps) {
    super(scope, id, props);
    const { base } = props;

    // --- OpenSearch Serverless ---------------------------------------------------------------
    const encryption = new aoss.CfnSecurityPolicy(this, 'AossEncryption', {
      name: 'rp-units-enc',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [{ ResourceType: 'collection', Resource: [`collection/${AOSS_COLLECTION}`] }],
        AWSOwnedKey: true,
      }),
    });
    // Lambdas outside the VPC call the collection endpoint; IAM + the data access policy gate it.
    const network = new aoss.CfnSecurityPolicy(this, 'AossNetwork', {
      name: 'rp-units-net',
      type: 'network',
      policy: JSON.stringify([{
        Rules: [{ ResourceType: 'collection', Resource: [`collection/${AOSS_COLLECTION}`] }],
        AllowFromPublic: true,
      }]),
    });
    // NextGen is chosen through a collection group (a collection without one is Classic, with an OCU floor
    // billed around the clock). Min 0 OCU lets the group scale to zero while idle; the max caps the bill.
    // aws-cdk-lib 2.219 has no L1 for the group yet, so it is a raw CfnResource.
    const group = new CfnResource(this, 'CollectionGroup', {
      type: 'AWS::OpenSearchServerless::CollectionGroup',
      properties: {
        Name: AOSS_COLLECTION_GROUP,
        Generation: 'NEXTGEN',
        // NextGen groups require standby replicas (DISABLED is rejected); idle cost is still 0 OCU.
        StandbyReplicas: 'ENABLED',
        Description: 'Regionproof units (NextGen, scale to zero)',
        CapacityLimits: {
          MinIndexingCapacityInOcu: 0,
          MinSearchCapacityInOcu: 0,
          MaxIndexingCapacityInOcu: AOSS_MAX_OCU,
          MaxSearchCapacityInOcu: AOSS_MAX_OCU,
        },
      },
    });
    const collection = new aoss.CfnCollection(this, 'Collection', {
      name: AOSS_COLLECTION,
      type: 'VECTORSEARCH',
      standbyReplicas: 'ENABLED',
      description: 'Regionproof units: units-v1-0, units-v2-3-1',
    });
    collection.addPropertyOverride('CollectionGroupName', AOSS_COLLECTION_GROUP);
    collection.addDependency(group);
    collection.addDependency(encryption);
    collection.addDependency(network);
    this.aossEndpoint = collection.attrCollectionEndpoint;
    this.aossCollectionArn = collection.attrArn;

    // --- Neptune Serverless --------------------------------------------------------------------
    this.neptuneSg = new ec2.SecurityGroup(this, 'NeptuneSg', {
      vpc: base.vpc,
      securityGroupName: 'rp-neptune-sg',
      description: 'Neptune: 8182 from rp-lambda-sg only',
      allowAllOutbound: false,
    });
    this.neptuneSg.addIngressRule(base.lambdaSg, ec2.Port.tcp(8182), 'VPC Lambdas (SigV4)');
    const subnets = new neptune.CfnDBSubnetGroup(this, 'NeptuneSubnets', {
      dbSubnetGroupName: 'rp-graph-subnets',
      dbSubnetGroupDescription: 'Regionproof Neptune private isolated subnets',
      subnetIds: base.vpc.isolatedSubnets.map((s) => s.subnetId),
    });
    const cluster = new neptune.CfnDBCluster(this, 'Neptune', {
      dbClusterIdentifier: 'rp-graph',
      engineVersion: props.neptuneEngineVersion,
      iamAuthEnabled: true,
      storageEncrypted: true,
      kmsKeyId: base.key.keyArn,
      dbSubnetGroupName: subnets.ref,
      vpcSecurityGroupIds: [this.neptuneSg.securityGroupId],
      serverlessScalingConfiguration: { minCapacity: 1, maxCapacity: 2.5 },
      deletionProtection: false,
      backupRetentionPeriod: 1,
    });
    cluster.addDependency(subnets);
    const instance = new neptune.CfnDBInstance(this, 'NeptuneInstance', {
      dbInstanceIdentifier: 'rp-graph-1',
      dbClusterIdentifier: cluster.ref,
      dbInstanceClass: 'db.serverless',
    });
    instance.addDependency(cluster);
    this.neptuneEndpoint = Fn.join(':', [cluster.attrEndpoint, cluster.attrPort]);
    this.neptuneResourceId = cluster.attrClusterResourceId;

    // --- C admin functions (VPC) ----------------------------------------------------------------
    const fns = buildFunctions(this, 'knowledge', base, {
      neptuneEndpoint: this.neptuneEndpoint,
      neptuneResourceId: this.neptuneResourceId,
      aossCollectionArn: this.aossCollectionArn,
    });
    for (const fn of Object.values(fns.byName)) fn.node.addDependency(instance);
    addAossDataPolicy(this, 'rp-units-admin', fns.aossPrincipals, props.aossAdminArns ?? []);

    new ssm.StringParameter(this, 'AossEndpointParam', { parameterName: SSM.aossEndpoint,
      stringValue: this.aossEndpoint });
    new ssm.StringParameter(this, 'NeptuneEndpointParam', { parameterName: SSM.neptuneEndpoint,
      stringValue: this.neptuneEndpoint });
    new CfnOutput(this, 'AossEndpoint', { value: this.aossEndpoint });
    new CfnOutput(this, 'NeptuneEndpoint', { value: this.neptuneEndpoint });
  }
}
