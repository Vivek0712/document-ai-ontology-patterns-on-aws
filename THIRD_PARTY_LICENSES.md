# Third-party licenses

This repository is licensed under the [Apache License 2.0](LICENSE). It also uses third-party
software and data. This page lists them, grouped by how they reach you:

1. [Files included in this repository](#1-files-included-in-this-repository)
2. [Packages bundled into artifacts you build](#2-packages-bundled-into-artifacts-you-build)
3. [Software pulled at deploy time or at run time](#3-software-pulled-at-deploy-time-or-at-run-time)
4. [Development and test tools](#4-development-and-test-tools)
5. [AWS services and foundation models](#5-aws-services-and-foundation-models)
6. [Notes on non-permissive licenses](#6-notes-on-non-permissive-licenses)
7. [How to regenerate this list](#7-how-to-regenerate-this-list)

Versions are the ones pinned in the lock and requirement files at the time of writing. If you change a
pin, re-run the commands in section 7. This page is a summary to help you comply. The license text that
ships with each package is the authority.

---

## 1. Files included in this repository

These files are unmodified upstream copies. Their own licenses apply. `ontology/build.py` adds one
version triple per named graph to the *built* N-Quads. It does not edit these files.

| Component | Path | Version / pin | License | Attribution required |
|---|---|---|---|---|
| FIBO, Financial Industry Business Ontology (6 modules: Contracts, People, LegalPersons, Debt, DebtInstruments, Bonds) | `ontology/src/fibo/` | `edmcouncil/fibo` commit `2d73435a4419b66b7c9f44cb9763e35fe8ac7890` (tag `master_2026Q3`) | [MIT](ontology/src/fibo/LICENSE) | Keep `ontology/src/fibo/LICENSE` and the copyright headers in each file (EDM Council, Inc.; Object Management Group, Inc.) |
| DoCO, the Document Components Ontology (SPAR Ontologies) | `ontology/src/doco/doco.ttl` | 1.4.0, version IRI `doco:2026-06-25` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Credit David Shotton and Silvio Peroni, SPAR Ontologies; link the license; say the file is unmodified |
| W3C SKOS RDF schema | `ontology/src/w3c/skos.rdf` | Namespace document 2009-08-18 | [W3C Software and Document License](https://www.w3.org/copyright/software-license-2023/) | Keep the notice |
| W3C PROV-O | `ontology/src/w3c/prov-o.ttl` | Recommendation 2013-04-30 | [W3C Software and Document License](https://www.w3.org/copyright/software-license-2023/) | Keep the notice |

`ontology/src/NOTICE.md` and `ontology/src/manifest.json` record the exact upstream URLs.

Everything else in the repository is original work under Apache-2.0. That includes the `fin:`, `doc:` and
`rp:` vocabularies in `ontology/src/rp/`, the sample PDFs, the gold regions, the recorded runs and the UI
fixtures. **All sample data is fictitious** (NewAge Global Fintech). Any resemblance to real people or
organizations is coincidental.

---

## 2. Packages bundled into artifacts you build

None of these packages are stored in the repository. They are installed from npm or PyPI when you build,
and they end up inside an artifact you deploy: the web UI bundle, a Lambda layer or the agent bundle. If
you redistribute those artifacts, their licenses travel with them.

### 2.1 Web UI bundle (`usecases/provenant/ui/dist`, built with Vite)

Direct runtime dependencies from `usecases/provenant/ui/package.json`, with their licenses from `usecases/provenant/ui/package-lock.json`:

| Package | Version | License |
|---|---|---|
| react, react-dom | 18.3.1 | MIT |
| react-router-dom | 6.30.6 | MIT |
| @tanstack/react-query | 5.59.0 | MIT |
| @radix-ui/react-dialog, -dropdown-menu, -popover, -scroll-area, -slot, -switch, -tabs, -toggle-group, -tooltip | 1.x / 2.x | MIT |
| framer-motion | 11.11.17 | MIT |
| @dagrejs/dagre | 1.1.4 | MIT |
| d3-ease | 3.0.1 | BSD-3-Clause |
| d3-interpolate, d3-shape | 3.0.1, 3.2.0 | ISC |
| lucide-react | 0.453.0 | ISC |
| cmdk | 1.0.0 | MIT |
| sonner | 1.5.0 | MIT |
| clsx | 2.1.1 | MIT |
| tailwind-merge | 2.5.4 | MIT |
| class-variance-authority | 0.7.0 | Apache-2.0 |
| amazon-cognito-identity-js | 6.3.22 | Apache-2.0 |
| **@fontsource-variable/inter** (Inter typeface, Rasmus Andersson) | 5.1.0 | **SIL OFL 1.1** |
| **@fontsource-variable/jetbrains-mono** (JetBrains Mono typeface, JetBrains) | 5.1.0 | **SIL OFL 1.1** |

Transitive runtime packages (about 130 in total) are MIT, ISC, Apache-2.0 (`@aws-crypto/*`, `@aws-sdk/*`,
`@smithy/types`), BSD-2-Clause / BSD-3-Clause (`webidl-conversions`, `ieee754`, `d3-ease`) and 0BSD
(`tslib`). None are copyleft.

**Fonts.** The SIL Open Font License allows the fonts to be bundled and redistributed with software. You
may not sell the fonts on their own, and a modified font must not use the reserved font name. The OFL
text ships inside each `@fontsource-variable/*` package.

### 2.2 Lambda layers (`build/layers/*`, built by `scripts/build_lambdas.sh`)

`boto3` and `botocore` come from the AWS Lambda Python 3.12 runtime and are not bundled.

| Layer | Direct packages (pinned) | Licenses in the full dependency closure |
|---|---|---|
| `core` | pydantic 2.x, PyYAML 6.0.x | MIT, PSF-2.0 (`typing-extensions`) |
| `ingest` | pypdf 6.21.4, pypdfium2 5.14.0, pillow 13.0.0, amazon-textract-textractor 1.10.0, opensearch-py 3.2.0 | BSD-3-Clause (pypdf, idna, protobuf), BSD-3-Clause and Apache-2.0 (pypdfium2, which also bundles PDFium and its dependency licenses), MIT-CMU (pillow), Apache-2.0 (textractor, textract-caller, response-parser, opensearch-py, opensearch-protobufs, grpcio, requests), MIT (marshmallow, tabulate, rapidfuzz, urllib3, charset-normalizer, six), BSD-2-Clause (XlsxWriter), **MPL-2.0 (certifi)** |
| `agent` | PyJWT[crypto] 2.15.1 | MIT (PyJWT), Apache-2.0 OR BSD-3-Clause (cryptography), MIT-0 (cffi), BSD-3-Clause (pycparser) |
| `api` | none beyond `core` | |

### 2.3 Agent bundle for Amazon Bedrock AgentCore Runtime (built by `scripts/deploy_agentcore.sh`)

| Direct packages (pinned) | License |
|---|---|
| strands-agents 1.63.0 | Apache-2.0 |
| bedrock-agentcore 1.25.2 | Apache-2.0 |
| mcp 2.3.0 | MIT |
| PyJWT[crypto] 2.15.1 | MIT |
| pydantic 2.14.2, PyYAML 6.0.3 | MIT |
| boto3 1.43.123 | Apache-2.0 |

The closure adds Apache-2.0 (OpenTelemetry, watchdog, python-multipart, s3transfer), MIT (anyio, attrs,
jsonschema, referencing, rpds-py, h11, truststore, docstring-parser), BSD-3-Clause (starlette,
sse-starlette, uvicorn, httpx, httpcore, click, websockets, idna), BSD-2-Clause (wrapt) and
**MPL-2.0 (certifi)**.

### 2.4 AWS CDK app (`infra/`)

| Package | Version | License |
|---|---|---|
| aws-cdk-lib | 2.219.0 | Apache-2.0 (its own `ThirdPartyNoticeText.txt` lists the packages it bundles) |
| constructs | 10.4.2 | Apache-2.0 |

`aws-cdk-lib` bundles `case@1.6.3`, which is dual-licensed `MIT OR GPL-3.0-or-later`. This project uses it
under MIT.

### 2.5 Local pipeline, data generator and evaluation (run on your machine, not deployed)

| Package | Version | License |
|---|---|---|
| rdflib | 7.2.1 | BSD-3-Clause |
| pyoxigraph | 0.5.13 | MIT OR Apache-2.0 |
| reportlab | 4.4.4 | BSD-3-Clause |
| pycognito | 2024.5.1 | Apache-2.0 |
| joserfc | >= 1.0 | BSD-3-Clause |

---

## 3. Software pulled at deploy time or at run time

| Component | When | License | Notes |
|---|---|---|---|
| `docling-serve-cpu` container image (`quay.io/docling-project/docling-serve-cpu`, pinned by digest in `infra/cdk.json`) | Pulled by Amazon ECS on AWS Fargate at deploy | MIT (docling-serve) | The image contains its own OS packages and Python dependencies under their own licenses. To list them, generate a software bill of materials for the pinned digest, for example `syft <image>@<digest>` |
| `docling` 2.145.0 and its closure (optional local install, `services/local_pipeline/requirements-docling.txt`, about 1.4 GB) | `pip install` on your machine | MIT (docling, docling-core, docling-parse, docling-ibm-models); Apache-2.0 (transformers, huggingface-hub, accelerate, safetensors, tokenizers, opencv-python-headless, rapidocr); BSD (torchvision, scipy, pandas, numpy); mixed permissive (torch); **MPL-2.0 (tqdm, certifi)** | Optional. Without it the local pipeline uses a clearly labeled text-layer fallback |
| Docling layout model `docling-project/docling-layout-heron` (Docling's default layout model) | Downloaded from Hugging Face by `docling-ibm-models` on first local use; the docling-serve image ships its own copy | Apache-2.0 (model card, snapshot `8f39ad3`) | Model weights are licensed separately from the Python code. If you pin a different layout model (for example an `egret` variant), check its model card |
| Docling table-structure model (TableFormer) `docling-project/docling-models` | As above | CDLA-Permissive-2.0 (model card, snapshot `fc0f2d4`) | Permissive data license. Keep the license notice if you redistribute the weights. This repository does not redistribute them |
| RapidOCR 3.10.0 and its bundled PP-OCR model files (local OCR for scanned pages) | Installed with the optional Docling extras | Apache-2.0 (code and models; model copyright Baidu / PaddleOCR rights holders, see the package's `MODEL_LICENSES.md`) | Used only by the local stand-in for Amazon Textract |

---

## 4. Development and test tools

These tools are used to build, lint and test. They are not part of any artifact you deploy.

| Tool | License | Note |
|---|---|---|
| pytest, ruff, iniconfig, pluggy | MIT | |
| moto 5.x (AWS service mocks) | Apache-2.0 | Tests run against moto. No real AWS calls |
| boto3 / botocore | Apache-2.0 | |
| shellcheck-py | MIT (wrapper) | Downloads the **ShellCheck binary, which is GPL-3.0**. Used only as a local lint tool. Not distributed |
| TypeScript, Vite, Vitest, Jest, ts-jest, ts-node, Tailwind CSS, PostCSS, Autoprefixer | MIT / Apache-2.0 | |
| aws-cdk (CLI) | Apache-2.0 | |
| @playwright/test, playwright-core | Apache-2.0 | |
| **axe-core, @axe-core/playwright** | **MPL-2.0** | Accessibility tests only. Used unmodified |
| caniuse-lite | CC BY 4.0 | Browser support data used by the build tool chain |
| type-fest | MIT OR CC0-1.0 | |
| exit 0.1.2 (transitive, via Jest) | MIT | The lock file has no license field; the package's own `package.json` declares MIT in the legacy `licenses` array |
| gitleaks (CI and pre-commit) | MIT | |
| pre-commit, pre-commit-hooks | MIT | |

---

## 5. AWS services and foundation models

The deployed application calls AWS services. Those are not third-party software included in this
repository. Your use of them is governed by your agreement with AWS, the
[AWS Service Terms](https://aws.amazon.com/service-terms/) and, for models in Amazon Bedrock, the
end-user license agreement of each model provider, which you accept when you enable model access in the
Amazon Bedrock console.

The model ids used by default are listed in `config/models.yaml` (Amazon Titan Text Embeddings V2, Amazon
Nova, Anthropic Claude). Recorded model output in `runs/local/` was generated over fictitious data, through Amazon Bedrock, under the
AWS Service Terms and each model provider's terms that applied at the time. It is included as evaluation
evidence. Before you reuse model output from this repository in another product or dataset, check the current
terms of the model provider (Anthropic for Claude, Amazon for Amazon Nova and Amazon Titan).

---

## 6. Notes on non-permissive licenses

No AGPL, SSPL or other network-copyleft license was found in any manifest or lock file.

| License | Packages | What it means for you |
|---|---|---|
| MPL-2.0 (file-level copyleft) | certifi, tqdm, axe-core | Used unmodified. If you modify one of *their* files and distribute it, publish that file's changes under MPL-2.0. Your own code is not affected |
| CC BY 4.0 | DoCO (included), caniuse-lite (dev) | Keep the attribution in NOTICE and in this file |
| SIL OFL 1.1 | Inter, JetBrains Mono | Bundling is allowed; see 2.1 |
| GPL-3.0 | ShellCheck binary (dev tool), `case` (dual-licensed, MIT chosen) | Not distributed |

---

## 7. How to regenerate this list

Run these from a clone after installing dependencies. They only read lock files and installed metadata.

```bash
# npm: runtime (non-dev) packages and licenses, from the lock file
for d in usecases/provenant/ui infra; do
  (cd "$d" && node -e '
    const l = require("./package-lock.json").packages;
    for (const [k, v] of Object.entries(l)) if (k && !v.dev)
      console.log(k.replace(/.*node_modules\//, "") + "@" + v.version + "\t" + (v.license || "UNKNOWN"));' | sort -u)
done

# Python: every installed distribution and its license
.venv/bin/pip install pip-licenses
.venv/bin/pip-licenses --from=mixed --format=markdown --with-urls --order=license
```

Review any `UNKNOWN`, GPL, AGPL, LGPL, MPL, SSPL or CC license before you add a dependency. The pull
request template asks for this.
