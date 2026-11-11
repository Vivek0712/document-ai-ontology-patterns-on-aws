# Contributing

Thank you for your interest in this project. Bug reports, fixes, documentation, new examples and new use
cases are all welcome.

This page covers:

- [Ways to contribute](#ways-to-contribute)
- [Set up a development environment](#set-up-a-development-environment)
- [Before you open a pull request](#before-you-open-a-pull-request)
- [Coding standards](#coding-standards)
- [Data, privacy and secrets](#data-privacy-and-secrets)
- [Adding a dependency](#adding-a-dependency)
- [Adding a use case](#adding-a-use-case)
- [Licensing of contributions](#licensing-of-contributions)

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). To report a security issue,
do **not** open a public issue. Follow [SECURITY.md](SECURITY.md) instead.

---

## Ways to contribute

| You want to | Do this |
|---|---|
| Report a bug | Open a **Bug report** issue. Include the command you ran, the output and your versions |
| Ask how to apply a pattern to your documents | Open a **Question / pattern adoption** issue |
| Propose a feature or a new pattern variant | Open a **Feature request** issue first, so we can agree on the approach before you write code |
| Fix a typo or a doc | Open a pull request directly |
| Add a use case | Read [Adding a use case](#adding-a-use-case), then open a feature request |

For anything larger than a small fix, please open an issue first. It saves you rework.

---

## Set up a development environment

You need:

- Python 3.12
- Node.js 22 and npm
- GNU Make and Bash
- Optional: the AWS CLI v2 and AWS credentials, only for the targets that deploy or call AWS

```bash
git clone https://github.com/Vivek0712/document-ai-ontology-patterns-on-aws.git
cd document-ai-ontology-patterns-on-aws

make venv PY=python3.12                      # .venv with dev and test requirements
(cd usecases/provenant/ui && npm ci)       # web UI
(cd infra && npm ci)                         # AWS CDK app

pip install pre-commit       # or: pipx install pre-commit
pre-commit install           # secret scan and hygiene checks on every commit
```

Run the offline quickstart to check your setup. It needs no AWS account:

```bash
make e2e-local PII=local     # local pipeline on the sample package, with the local PII stand-in
```

---

## Before you open a pull request

Every check below runs in CI, without AWS credentials. Run them locally first.

```bash
.venv/bin/ruff check .                         # Python lint
.venv/bin/python -m pytest                     # Python tests (moto, no AWS calls)
make contracts && git diff --exit-code contracts/generated   # contract schemas are in sync

(cd usecases/provenant/ui && npm run typecheck && RP_ALLOW_MISSING_FIXTURES=1 npm test && npm run build:mock)
(cd infra && npx tsc --noEmit && npx jest)

pre-commit run --all-files                     # gitleaks, large files, generated paths, US spelling in docs
python3 scripts/dev/check-doc-links.py         # relative links, anchors and cited run ids in the docs
```

Then check that:

- The test count did not go down. If you removed a test, say why in the pull request.
- The pipeline outputs did not change unless you meant them to. Pipeline outputs (units, graph facts,
  answers) are deterministic. If you change them on purpose, say so in the pull request and update the
  checked-in fixtures in the same commit.
- `CHANGELOG.md` has a line under **Unreleased** for any change a user would notice.
- New behavior has a test, and new configuration has a line in `.env.example` or the relevant README.

Keep pull requests focused. One logical change per pull request is easier to review and to revert.

### Changes that call AWS

Do not add tests that call AWS to the default test run. CI has no AWS credentials, by design. If a change
needs a live check (for example a new Amazon Bedrock model id), describe the check you ran, the Region
and the approximate cost in the pull request. Never paste account ids, ARNs or console screenshots that
show them.

---

## Coding standards

**Python**

- Python 3.12. Formatting and lint rules are in `pyproject.toml` (ruff, line length 120).
- Type hints on public functions. Pydantic models for anything that crosses a process or service boundary.
- No module-level AWS clients that run at import time in library code. Create clients lazily, so the code
  can be imported and tested without credentials.
- Read configuration from environment variables or the config files under `config/`. Do not hard-code
  account ids, Regions, profile names, bucket names or endpoints.

**TypeScript**

- Strict mode as configured in each `tsconfig.json`. `npm run typecheck` must pass.
- AWS CDK: keep construct ids stable. Changing a construct id makes AWS CloudFormation replace the
  resource, which can delete data (buckets, tables). If you must rename, pin the old logical id.
- No `cdk.context.json` and no `cdk.out/` in commits. Synthesis must work with
  `-c rp:account=000000000000` and no AWS credentials.

**Shell**

- `set -euo pipefail`. Scripts must pass ShellCheck (`make lint`).
- Use `${AWS_PROFILE:+--profile "${AWS_PROFILE}"}` so the default AWS credential chain works when no profile
  is set.

**Comments and docs**

- Explain *why*, briefly. Link to a doc under `docs/` for the long version.
- Do not reference internal tickets, private documents or people. A public reader must be able to follow
  every reference.
- Use correct AWS service names: Amazon Bedrock, Amazon Bedrock AgentCore, Amazon Textract, Amazon
  Comprehend, AWS Lambda, AWS Step Functions, Amazon OpenSearch Serverless, Amazon Neptune, Amazon S3,
  Amazon DynamoDB, AWS CDK.
- State costs as estimates, with the pricing page you used and the date you checked it.

---

## Data, privacy and secrets

- **Fictitious data only.** Do not commit real documents, real names, real account numbers or any personal
  data, even redacted. Use the generator in `data/` to create new sample documents. Every generated page
  carries a fictitious-data notice. Keep it.
- **No secrets or account identifiers.** That includes access keys, session tokens, passwords, AWS account
  ids, real ARNs, deployed endpoint URLs, Amazon Cognito pool ids, AWS CLI profile names and home-directory
  paths. Use placeholders such as `000000000000`, `111111111111` or `<your-profile>`.
- **Generated output stays out of git.** `cdk.out/`, `build/`, `dist/`, `node_modules/`, `.venv/` and
  `usecases/provenant/ui/public/config.json` can contain account-specific values. They are in `.gitignore`,
  and pre-commit blocks them, also when you force-add them.
- **Runs exported from AWS stay out of git.** `make export-runs` copies run records from a deployed stack into
  `runs/`. Those records hold real Amazon Cognito user ids, session ids and error messages that can include ARNs.
  Only `runs/local/` is tracked. Never force-add anything else under `runs/` unless you have scrubbed it.
- **Document metadata counts too.** PDF and image metadata (author, creator, file paths) is checked by
  `scripts/dev/check-binary-metadata.sh`. Generate sample documents with the generator in `data/`, which writes
  a fictitious-data notice into the metadata.
- The pre-commit hooks and CI run [gitleaks](https://github.com/gitleaks/gitleaks) with the rules in
  `.gitleaks.toml`. If a hook flags a false positive, add the narrowest possible allowlist entry and explain
  it in the pull request. Do not disable the hook.

If you committed a secret by mistake, rotate it first, then tell the maintainers (see
[SECURITY.md](SECURITY.md)). Removing it from the latest commit is not enough; it stays in git history.

---

## Adding a dependency

1. Pin an exact version in the relevant requirements file or `package.json`.
2. Check its license, and the licenses of what it pulls in. Permissive licenses (MIT, BSD, Apache-2.0,
   ISC) are fine. Discuss anything else in the issue first. AGPL, SSPL and similar licenses cannot be
   accepted.
3. Update [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Section 7 of that file has the commands.
4. Prefer the Python standard library or an existing dependency when the gain is small.

---

## Adding a use case

A use case is a set of documents, an ontology extension, pipeline and model configuration, prompts and
golden questions that runs on the shared building blocks. Provenant, in `usecases/provenant/`, is the
reference example.

1. Open a feature request describing the documents, the questions and why the patterns fit.
2. Follow [Build your own use case](docs/quickstarts/10-build-your-own-use-case.md). In this release a use
   case's domain files still sit at the repository root; [usecases/README.md](usecases/README.md) lists them.
3. Use generated, fictitious documents. Include gold regions so the evaluation can score region recall and
   citation correctness.
4. Add a README that states what is real AWS and what is a local stand-in, and the estimated cost of a run.

---

## Licensing of contributions

This project is licensed under the [Apache License 2.0](LICENSE). Under section 5 of that license, any
contribution you intentionally submit for inclusion is licensed under the same terms, without additional
conditions. Only submit work that you have the right to license this way.

If your contribution includes third-party material, say so in the pull request, include its license, and
add it to [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) and, if it is copied into the repository,
to [NOTICE](NOTICE).
