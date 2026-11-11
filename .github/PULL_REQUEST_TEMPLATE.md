## What this changes

<!-- One or two sentences. Link the issue: "Closes #123". -->

## Why

<!-- The problem this solves. -->

## Type of change

- [ ] Bug fix
- [ ] New feature or pattern variant
- [ ] New or updated use case
- [ ] Documentation
- [ ] Build, CI or dependencies

## Impact

- [ ] Pipeline outputs (units, graph facts, answers) are **unchanged**, or the change is intended and the
      checked-in fixtures are updated in this PR
- [ ] AWS resources are unchanged, or construct ids are kept stable (a changed id replaces the resource and
      can delete data)
- [ ] No change to who can see which documents, or the change is described below
- [ ] Cost impact is none, or is described below as an estimate with its source

<!-- Describe any box you could not tick. -->

## How I tested it

<!-- Commands you ran. CI runs without AWS credentials; if you also ran something against AWS, say what,
     in which Region, and the approximate cost. Never include account ids or ARNs. -->

- [ ] `ruff check .` and `pytest` pass, and the test count did not go down
- [ ] UI: `npm run typecheck`, `npm test` (if the UI changed)
- [ ] Infra: `npx tsc --noEmit`, `npx jest`, `cdk synth -c rp:account=000000000000` (if infra changed)
- [ ] `make contracts` produces no diff (if models changed)

## Hygiene

- [ ] No credentials, account ids, real ARNs, endpoint ids, profile names, home paths or personal data
      (`pre-commit run --all-files` passes)
- [ ] Sample data is fictitious and keeps the fictitious-data notice
- [ ] New dependencies are pinned, permissively licensed, and listed in `THIRD_PARTY_LICENSES.md`
- [ ] User-visible changes have a line under **Unreleased** in `CHANGELOG.md`
- [ ] Docs and `.env.example` are updated for new behavior or configuration

By submitting this pull request, I confirm that my contribution is made under the terms of the Apache
License 2.0.
