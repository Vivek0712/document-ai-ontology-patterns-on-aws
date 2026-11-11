/** OpenSearch Serverless data-access policy helper (one policy per consuming stack). */
import { Stack } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as aoss from 'aws-cdk-lib/aws-opensearchserverless';
import { Construct } from 'constructs';

export const AOSS_COLLECTION = 'rp-units';
export const AOSS_INDEX_PATTERN = 'units-v*';

const READ = ['aoss:DescribeIndex', 'aoss:ReadDocument'];
/** Read and delete documents (rp-api's package/document delete); no CreateIndex or UpdateIndex. */
const DELETE = ['aoss:DescribeIndex', 'aoss:ReadDocument', 'aoss:WriteDocument'];
const WRITE = ['aoss:CreateIndex', 'aoss:DescribeIndex', 'aoss:UpdateIndex', 'aoss:ReadDocument',
  'aoss:WriteDocument'];

export function addAossDataPolicy(
  scope: Construct,
  name: string,
  principals: { role: iam.IRole; mode: 'read' | 'delete' | 'write' }[],
  extraAdminArns: string[] = [],
): aoss.CfnAccessPolicy | undefined {
  const readers = principals.filter((p) => p.mode === 'read').map((p) => p.role.roleArn);
  const deleters = principals.filter((p) => p.mode === 'delete').map((p) => p.role.roleArn);
  const writers = principals.filter((p) => p.mode === 'write').map((p) => p.role.roleArn).concat(extraAdminArns);
  const rules: object[] = [];
  const statement = (arns: string[], perms: string[]): object => ({
    Description: `${name} ${perms === READ ? 'read' : perms === DELETE ? 'delete' : 'write'}`,
    Rules: [
      { ResourceType: 'collection', Resource: [`collection/${AOSS_COLLECTION}`],
        Permission: ['aoss:DescribeCollectionItems'] },
      { ResourceType: 'index', Resource: [`index/${AOSS_COLLECTION}/${AOSS_INDEX_PATTERN}`], Permission: perms },
    ],
    Principal: arns,
  });
  if (readers.length) rules.push(statement(readers, READ));
  if (deleters.length) rules.push(statement(deleters, DELETE));
  if (writers.length) rules.push(statement(writers, WRITE));
  if (!rules.length) return undefined;
  if (name.length > 32) throw new Error(`AOSS policy name too long: ${name}`);
  return new aoss.CfnAccessPolicy(scope, `AossData${name.replace(/[^A-Za-z0-9]/g, '')}`, {
    name,
    type: 'data',
    description: `Regionproof ${name}`,
    policy: Stack.of(scope).toJsonString(rules),
  });
}
