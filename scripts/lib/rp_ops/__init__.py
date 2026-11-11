"""Operator helpers behind scripts/*.sh. Run as ``python -m rp_ops <command>``.

The shell scripts own ordering, confirmation prompts and logging; this package does the AWS work
that is easier to get right in boto3 than in the CLI (DynamoDB pagination, SigV4 to OpenSearch
Serverless, Cognito SRP, AgentCore control plane).
"""
