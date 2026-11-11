/**
 * rp-web: UI bucket + CloudFront (OAC). `/api/*` is a second behavior to the REST API (stage v1, the
 * `/api` prefix stripped by a CloudFront Function), so the browser calls the API on its own origin and
 * the Authorization header and SSE responses pass through uncached. Writes `/config.json` for the SPA
 * and sets the data bucket CORS for presigned PUT/GET from the web origin.
 */
import * as fs from 'fs';
import * as path from 'path';
import { Annotations, CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { REPO_ROOT, SSM } from './names';

export interface WebStackProps extends StackProps {
  api: apigw.RestApi;
  dataBucket: s3.IBucket;
  userPoolId: string;
  userPoolClientId: string;
  devOrigins: string[];
  uiDistDir?: string;
}

const STRIP_API_PREFIX = `function handler(event) {
  var req = event.request;
  req.uri = req.uri.replace(/^\\/api(?=\\/|$)/, '') || '/';
  return req;
}`;

/**
 * SPA routing on the UI behavior only. Distribution-level errorResponses would also rewrite the API's
 * 403/404 (e.g. "You don't have access to this region.") into index.html with 200, so deep links are
 * rewritten here instead. Only real files pass through: anything under /assets/ and paths ending in a
 * static-file extension. Everything else is a client route and serves /index.html, including routes
 * whose ids contain dots (`/u/<docId>:2.3.1:eu-03`, `/region/...`, `/pipelines/2.3.1`).
 */
export const SPA_REWRITE = `function handler(event) {
  var req = event.request;
  var uri = req.uri;
  var isFile = uri.indexOf('/assets/') === 0
    || /\\.(js|mjs|css|json|map|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|txt|webmanifest|html)$/i.test(uri);
  if (!isFile) { req.uri = '/index.html'; }
  return req;
}`;

export class WebStack extends Stack {
  readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);

    const key = new kms.Key(this, 'UiKey', {
      alias: 'alias/rp-ui',
      description: 'Regionproof UI bucket (CloudFront OAC decrypts)',
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const uiBucket = new s3.Bucket(this, 'UiBucket', {
      bucketName: `rp-ui-${this.account}-${this.region}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: key,
      bucketKeyEnabled: true,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const stripApi = new cloudfront.Function(this, 'StripApiPrefix', {
      functionName: 'rp-strip-api-prefix',
      code: cloudfront.FunctionCode.fromInline(STRIP_API_PREFIX),
      runtime: cloudfront.FunctionRuntime.JS_2_0,
    });
    const spaRewrite = new cloudfront.Function(this, 'SpaRewrite', {
      functionName: 'rp-spa-rewrite',
      code: cloudfront.FunctionCode.fromInline(SPA_REWRITE),
      runtime: cloudfront.FunctionRuntime.JS_2_0,
    });
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Regionproof UI + /api',
      defaultRootObject: 'index.html',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(uiBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        compress: true,
        functionAssociations: [{ function: spaRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(props.api, { readTimeout: Duration.seconds(60) }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          compress: false, // keep SSE frames unbuffered
          functionAssociations: [{ function: stripApi, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
        },
      },
      // No errorResponses: they are distribution-wide and would mask API 403/404 bodies (see SPA_REWRITE).
    });
    const webUrl = `https://${this.distribution.distributionDomainName}`;
    Annotations.of(this).acknowledgeWarning('@aws-cdk/aws-cloudfront-origins:wildcardKeyPolicyForOac',
      'UI key is used only by this bucket; the key policy is scoped to this account and CloudFront service');

    // --- UI content + runtime config ------------------------------------------------------------------
    const dist = props.uiDistDir ?? path.join(REPO_ROOT, 'usecases', 'provenant', 'ui', 'dist');
    let assets: s3deploy.BucketDeployment | undefined;
    if (fs.existsSync(path.join(dist, 'index.html'))) {
      assets = new s3deploy.BucketDeployment(this, 'UiAssets', {
        destinationBucket: uiBucket,
        sources: [s3deploy.Source.asset(dist, { exclude: ['config.json'] })],
        exclude: ['config.json'],
        prune: true,
        distribution: this.distribution,
        distributionPaths: ['/*'],
        memoryLimit: 512,
      });
    }
    const config = new s3deploy.BucketDeployment(this, 'UiConfig', {
      destinationBucket: uiBucket,
      sources: [s3deploy.Source.jsonData('config.json', {
        apiUrl: '/api',
        directApiUrl: props.api.url,
        region: this.region,
        userPoolId: props.userPoolId,
        userPoolClientId: props.userPoolClientId,
        webUrl,
        pipelineVersions: ['1.0', '2.3.1'],
      })],
      prune: false,
      distribution: this.distribution,
      distributionPaths: ['/config.json'],
      cacheControl: [s3deploy.CacheControl.noCache()],
    });
    if (assets) config.node.addDependency(assets);

    // --- data bucket CORS for presigned PUT (upload) and GET (page images) ---------------------------------
    const corsRules = [{
      AllowedOrigins: [webUrl, ...props.devOrigins],
      AllowedMethods: ['PUT', 'GET', 'HEAD'],
      AllowedHeaders: ['*'],
      ExposeHeaders: ['ETag', 'x-amz-version-id'],
      MaxAgeSeconds: 3000,
    }];
    new cr.AwsCustomResource(this, 'DataBucketCors', {
      onUpdate: {
        service: 'S3',
        action: 'putBucketCors',
        parameters: { Bucket: props.dataBucket.bucketName, CORSConfiguration: { CORSRules: corsRules } },
        physicalResourceId: cr.PhysicalResourceId.of(`rp-data-cors-${props.dataBucket.bucketName}`),
      },
      onDelete: {
        service: 'S3',
        action: 'deleteBucketCors',
        parameters: { Bucket: props.dataBucket.bucketName },
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([new iam.PolicyStatement({
        actions: ['s3:PutBucketCORS', 's3:GetBucketCORS'],
        resources: [props.dataBucket.bucketArn],
      })]),
      installLatestAwsSdk: false,
    });

    new ssm.StringParameter(this, 'UiBucketParam', { parameterName: SSM.uiBucket, stringValue: uiBucket.bucketName });
    new ssm.StringParameter(this, 'WebUrlParam', { parameterName: SSM.webUrl, stringValue: webUrl });
    new CfnOutput(this, 'WebUrl', { value: webUrl });
    new CfnOutput(this, 'UiBucketName', { value: uiBucket.bucketName });
  }
}
