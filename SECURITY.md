# Security

## Reporting a vulnerability

**Do not open a public GitHub issue for a security problem.**

Report it privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
open the **Security** tab of this repository and choose **Report a vulnerability**.

<!-- Owner: if this repository is published under an AWS-owned GitHub organization, replace the paragraph
above with: "If you discover a potential security issue in this project, notify AWS Security via the
vulnerability reporting page: https://aws.amazon.com/security/vulnerability-reporting/ ." -->

Please include:

- what you found and where (file, line, or deployed component)
- steps to reproduce, or a proof of concept that uses only the fictitious sample data
- the impact you expect
- whether the issue is in this repository's code or in an upstream dependency or AWS service

Please do **not** include real personal data, real credentials, or your AWS account id in the report.

We aim to acknowledge reports within 5 working days and to agree a disclosure date with you. This is a
community-maintained sample, so there is no formal service level. Vulnerabilities in AWS services
themselves should go to the [AWS vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/).
Vulnerabilities in a third-party dependency should go to that project's maintainers first.

## Supported versions

Only the latest release and the `main` branch receive fixes.

## Known dependency advisories

`npm ci` reports these advisories at the time of this release. Each is either not reachable in this sample or
waits for a major-version upgrade:

| Package | Where | Why it is not fixed yet |
|---|---|---|
| `react-router`, `react-router-dom` 6.30.6 (2 moderate) | UI | The fixes are only in React Router 7, a breaking upgrade. The advisories cover server-side rendering hydration, which the UI does not use (it is a static single-page app), and open redirects through untrusted URLs passed to `<Link>` or `navigate`, which the UI never does: every route it navigates to is built in code. |
| `aws-cdk-lib` 2.219.0 and the packages it bundles (`minimatch`, `brace-expansion`, `fast-uri`, `ajv`, `yaml`) | `infra/`, synth time only | The `aws-cdk-lib` advisories are for `NodejsFunction` bundling and AWS CodeBuild log encryption; the stacks use neither. The bundled packages run only on your machine during `cdk synth`, on inputs from this repository. The reference deployment was tested with this version; upgrading changes the synthesized templates, so it ships in a later release. |
| `deepmerge` (low) | `infra/`, through `jest` | Test tooling only. |

Dependabot is configured for npm, pip and GitHub Actions, so upgrades arrive as pull requests.

---

## This is sample code

This repository shows architectural patterns. It is **not** a production-ready service. Before you run it
with real documents, review it against your own security, privacy and compliance requirements. Security
in the cloud is a
[shared responsibility](https://aws.amazon.com/compliance/shared-responsibility-model/): AWS secures the
infrastructure, and you are responsible for how you configure and use the services and for the data you
put in them.

Deploying the AWS stacks creates billable resources in your account. Tear them down when you are done
(`make teardown`).

---

## Secrets and account identifiers

The repository contains no credentials, account ids or deployed endpoint ids, and it is set up to keep it
that way:

- **AWS credentials** come from the standard AWS credential chain (environment variables, `AWS_PROFILE`,
  IAM Identity Center, an instance or container role). Nothing in the code or config sets a profile name
  or an account id.
- **The target account** for AWS CDK comes from your active credentials (`CDK_DEFAULT_ACCOUNT`) or from
  `-c rp:account=...` on the command line. Do not write it into `cdk.json`.
- **The demo password** is generated at seed time and stored as an AWS Systems Manager Parameter Store
  `SecureString`. It is never written to disk or to the repository.
- **Generated files that hold account-specific values** (`infra/cdk.out/`, `cdk.context.json`, `build/`,
  `usecases/provenant/ui/public/config.json`, `.env`, runs exported from a deployed stack) are git-ignored. The
  pre-commit hooks refuse them even when they are force-added, and CI fails if one is tracked.
- **gitleaks** runs in pre-commit and in CI over the full history, with project rules for account ids,
  ARNs with a real account, profile names, home-directory paths and AWS endpoint ids (`.gitleaks.toml`).
- **Operator scripts** mask the account id (only the last four digits are shown) and the caller's user or
  session name when they print the caller identity.
- **Document metadata.** CI checks the metadata of tracked PDF and image files for personal names, home paths,
  e-mail addresses and account ids.

If you find a credential or a real identifier in this repository or its history, report it as described
above.

---

## Personal data (PII)

- **All sample data is fictitious.** The documents, names, tax ids and account numbers in `data/samples/`
  are generated. Tax ids use ranges that are never issued, and account numbers fail the Luhn check. Every
  page carries a fictitious-data notice.
- **The PII gate** runs during ingestion, before anything is embedded or indexed. On AWS it uses
  Amazon Comprehend `DetectPiiEntities`. Units that contain PII are masked or held for review according to
  the pipeline configuration in `config/pipelines/`. Held units are not indexed, so the agent cannot retrieve them.
- **The local stand-in is not a PII detector.** The offline quickstart (`PII=local`) uses a few regular
  expressions so you can run the pipeline without an AWS account. It is tuned to the fictitious samples
  and will miss real PII. Do not use it on real documents.
- **Recorded responses** in `runs/local/recorded/` are keyed by a SHA-256 hash of the request and store no
  input text.
- **Logs.** The AWS Lambda functions and the agent log stage status, ids and counts. Debug logging can include
  document text. Keep the default log level for real data, and set Amazon CloudWatch Logs retention and
  encryption to match your policy.
- **Your obligations.** If you process real personal data, you are responsible for the legal basis,
  retention, data-subject requests and Region choice. Review Amazon Comprehend's supported entity types
  and languages for your documents.

---

## Threat notes for the reference deployment

These notes describe the reference deployment in `infra/`. Use them as a starting point for your own threat
model, not as a complete one.

| Area | What the reference deployment does | What to consider for your use |
|---|---|---|
| **Access to documents** | The permission filter (caller groups) is injected by an AWS Lambda interceptor in front of the Amazon Bedrock AgentCore Gateway tools, from the verified Amazon Cognito access token. The model cannot set or widen it. The same filter is applied inside every retrieval sub-query, not after it. | Map your own entitlements to groups. Test that a user in one group can never retrieve, cite or open a region from another group's documents. |
| **Prompt injection from documents** | Documents are untrusted input. The agent can only call read-only tools. Every figure in an answer must cite a stored region, and figures that are not accounted for by a cited region are flagged. | Treat document text as data, never as instructions. Do not give the agent write tools without a human approval step. Consider Amazon Bedrock Guardrails. |
| **Authentication** | Amazon Cognito user pool, self sign-up off, strong password policy. The REST API uses a Cognito authorizer on every method. Demo users are created by the seed script. | Turn on MFA. Federate with your identity provider. Delete the demo users. |
| **Data at rest** | Amazon S3 and Amazon DynamoDB are encrypted with a customer managed AWS KMS key. S3 blocks public access and enforces TLS. Amazon Neptune uses IAM authentication in private isolated subnets. | Review key policies and rotation, and the bucket and table removal policies. |
| **Amazon OpenSearch Serverless network access** | To keep the sample simple and cheap, the collection's network policy allows public access. Data access is still limited by the data access policy to the pipeline and tool roles. | For real data, use a VPC endpoint network policy instead of public access. |
| **Web front end** | Amazon CloudFront with origin access control in front of a private S3 bucket. `/api/*` is routed to Amazon API Gateway, which has throttling configured. | Add AWS WAF and rate limits that match your expected traffic. |
| **Cost abuse** | API throttling. Operator scripts can pause the stack (scale AWS Fargate to zero, stop Neptune). | Set AWS Budgets alerts. Limit who can call the agent, because each question invokes Amazon Bedrock models. |
| **Supply chain** | Dependencies are pinned. The docling-serve container image is pinned by digest. CI runs gitleaks. | Enable Dependabot or a similar tool, scan container images (Amazon ECR image scanning or Amazon Inspector), and review license and security advisories before upgrading. |
| **Model output** | Answers show the cited regions so a reader can check them. Logged evaluation runs measure region recall and citation correctness. | Do not use an answer for a decision without checking the cited source. Re-run the evaluation when you change a model, prompt or pipeline version. |

For general guidance, see the
[Security Pillar of the AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)
and the
[Generative AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/generative-ai-lens.html).
