# Releasing palimem

Status: the pipeline and one release-candidate audit are verified (2026-10-05, nothing was published); the one-time setup below is not yet done. The pipeline is `.github/workflows/release.yml`. Nothing in this repository publishes anything by itself except a **published GitHub release**, and publishing uses PyPI Trusted Publishing (OIDC), so no API token is stored in GitHub or in the repository.

## One-time setup (the maintainer does this by hand)

### 1. PyPI trusted publisher

The `palimem` project already exists on PyPI (0.0.1 placeholder). At <https://pypi.org/manage/project/palimem/settings/publishing/> add a **GitHub** trusted publisher:

| Field | Value |
|---|---|
| Owner | `chleiva` |
| Repository name | `palimem` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Then revoke any long-lived account-wide API token you used for the first upload (<https://pypi.org/manage/account/token/>).

### 2. TestPyPI (for the manual dry run)

Create an account at <https://test.pypi.org>, add a *pending* trusted publisher for project `palimem` with the same owner, repository and workflow, and environment name `testpypi`.

### 3. GitHub environments

In the repository settings, under **Environments**, create `pypi` and `testpypi`. For `pypi`, add yourself as a required reviewer so every real release waits for a manual approval click, and restrict deployment to the `main` branch and tags `v*`.

### 4. Repository settings used by other workflows

- **Private vulnerability reporting**: Settings > Security > enable (referenced by `SECURITY.md`).
- **Dependabot alerts and security updates**: enable (config is in `.github/dependabot.yml`).
- **Code scanning**: the `security.yml` workflow uploads CodeQL results; enable code scanning in the same settings page.
- **Branch protection on `main`**: require the `ci` jobs (`test` matrix and `harness`) to pass before merging, as the differential gate is the merge gate.

## Release checklist

1. **Decide the version** with `docs/VERSIONING.md`: during 0.x a breaking contract change may go in a `0.MINOR`; a `0.MINOR.PATCH` may not break it. From 1.0, a breaking contract change is a package MAJOR.
2. **Changelog**: move `Unreleased` entries into a dated section in `CHANGELOG.md`; mark anything that breaks 0.x consumers **BREAKING** with a migration note.
3. **Bump** `version` in `pyproject.toml` and `__version__` in `src/palimem/__init__.py` (they must agree).
4. **Gates locally**: `ruff check .`, `mypy --strict src/palimem`, `python -m palimem.schemas --check`, `pytest -q` (also CI-style: with no study data and an empty `HOME`, where the data-backed tests must skip, not fail), `python -m harness.differential`, `python -m harness.kernel_diff --source-retract sidetable --strict --provenance strict` and `python -m harness.pipeline_diff --backend both --provenance strict` (full runs, minutes each), and the conformance suite (`python -m tests.conformance.runner --impl tests.conformance.impl_memory:MemoryImplementation`).
5. **Merge to `main`** and wait for CI (including the `harness` job) and the `security` workflow to be green.
6. **Dry run** (optional but recommended for the first release of each minor): Actions > `release` > *Run workflow*. Manual runs publish to **TestPyPI only**. Check the result with `pip install --index-url https://test.pypi.org/simple/ --no-deps palimem==<version>` in a clean environment.
7. **Tag and release**: create the tag `v<version>` (it must equal `v` plus the `pyproject.toml` version; the workflow fails otherwise) and publish a GitHub release for it. Publishing the release triggers the real PyPI upload, after the environment approval.
8. **Verify**: `pip install palimem==<version>` in a clean environment, `python -c "import palimem; print(palimem.__version__)"`, run the README quickstart and `palimem --version` from a directory that is not the repository, and check the project page. (The audit above is the dry run of this step against a local wheel.)
9. **Announce** only what the release contains; the README status table must match.

## Release candidate audit (2026-10-05, no release made)

A dry run of everything a release does short of publishing, so the first real release holds no surprises. The version was
left at 0.0.1. Commands run from the repository root with the dev extras installed; the build output went to a temporary
directory.

```bash
python -m build --outdir "$TMP/dist" .                          # sdist and wheel (built twice: before and after the pyproject fixes; the numbers below are from the final build)
python -m twine check "$TMP/dist"/*                             # both PASSED
python -m venv "$TMP/clean" && "$TMP/clean/bin/pip" install "$TMP/dist"/palimem-*.whl
cd "$TMP/work"                                                  # a directory that is not the repository
"$TMP/clean/bin/python" quickstart.py                           # the README quickstart
"$TMP/clean/bin/palimem" inspect | verify | explain | diff | export | import   # on the store the quickstart made
printf '<initialize, initialized, tools/list>' | "$TMP/clean/bin/palimem" mcp agent.db --principal agent:support --read-only
```

| Check | Result |
|---|---|
| Wheel contents | 72 Python files under `palimem/` (`admission`, `agent`, `compat`, `engine`, `extract`, `kernel`, `mcp`, `policy`, `store`, `types`) plus `palimem/prices.json` (package data, needed by the cost ledger) and `dist-info` with both licence files. **No** tests, benchmarks, harness, documentation, schemas or private files |
| Dependencies of the install | none: `pip list` in the clean environment shows `palimem` and `pip` only |
| Quickstart against the installed wheel | prints exactly what the README says (`established commit`, `unknown`, and the recall text) |
| CLI against the installed wheel | `--version`, `inspect`, `verify` (evidence log ok, stored beliefs ok), `explain` (single-origin marking shown), `diff` between two log positions, `export` (11 lines) and `import` into a new store (verified ok) all work |
| MCP over stdio against the installed wheel | `initialize` and `tools/list` answer; `--read-only` removes the write tools |
| `twine check` | wheel and sdist PASSED (the README renders as the long description) |
| Metadata (read from the final build's `METADATA`) | `Requires-Python >=3.11`, classifiers for 3.11 to 3.13, four project URLs, entry point `palimem = palimem.cli:main`, extras `bedrock`, `openai-compat` and `dev` only |

Findings, and what was done about each:

1. **The sdist included the 29 MB frozen-data cache (`.cache`, 1,501 files)** although `.cache/` is in `.gitignore`: hatchling
   did not honour the ignore for it. Fixed by explicit `exclude` entries in `pyproject.toml` (`.cache`, `.bench`, `.private`,
   `.claude`, `.venv`, `ledger`, `.env*`, scratch databases). After the fix the sdist is 1.1 MB (it was 3.4 MB) and contains
   no cache, database, ledger or env file. A release is built by CI from a clean checkout, where those directories do not
   exist, but the exclusion makes that true regardless of the working tree.
2. **Two extras were declared that nothing imports** (`mcp`, `anthropic`): the MCP server is a standard-library JSON-RPC loop
   and no Anthropic transport exists. They were removed so the metadata does not promise integrations that are not there.
3. **Classifiers and URLs** were missing for Python 3.11 to 3.13, audience, operating system, issues and changelog; added.
4. **Not fixed (outside the scope of this pass, which changes no source): the wheel has no `py.typed` marker.** The package
   passes `mypy --strict`, but a consumer's type checker will not use its annotations until a marker is added to
   `src/palimem/`.
5. **Not verified:** the installed wheel was exercised on Python 3.13 only (the machine's interpreter); the test suite runs
   on 3.11, 3.12 and 3.13 in CI, but a wheel install on 3.11 and 3.12 has not been run. The `release.yml` workflow has not
   been executed, because the trusted publisher is not configured and no release or TestPyPI upload was made.

## What 0.1.0 does and does not promise

This is the proposal to hold the first real release to; it changes nothing by itself. The numbering is the author's
decision: the release train in [`VERSIONING.md`](VERSIONING.md) section 4 was written before the work, and what exists now
spans what the train spread over 0.1 to 0.4, with the gaps listed in [`LIMITATIONS.md`](LIMITATIONS.md).

**It promises**

- `pip install palimem` installs with no dependencies on Python 3.11 or newer, and the README quickstart, the agent tool
  API, the MCP server over stdio and the `palimem` CLI work as documented in [`AGENT_GUIDE.md`](AGENT_GUIDE.md).
- The full pipeline answers all 30,272 queries of the 500 frozen Setting 1 streams with 0 disagreements against the study's
  frozen gold on status, value, alternatives and (through the compat projection) provenance, and this is checked in CI
  ([`PIPELINE.md`](PIPELINE.md), [`HARNESS.md`](HARNESS.md)).
- The log is append-only and hash-chained, appends are transactional and idempotent, `verify_log` detects edits, deletions and
  reordering, and an erasure leaves a tombstone that keeps the chain intact ([`STORAGE.md`](STORAGE.md)).
- An LLM reaching palimem through the agent tool API cannot choose source, origin or actor, cannot retract external evidence
  and cannot dispute without a host grant (18 of 20 trust-boundary fixtures pass; the other two have recorded causes).
- Stored data written by one 0.x release stays readable by migration in the next ([`VERSIONING.md`](VERSIONING.md) section 4).

**It does not promise**

- A stable contract: any `0.MINOR` may break it, marked **BREAKING** in the changelog with a migration note.
- The performance targets: four of the seven declared targets are missed at the sizes reached, and no run reached the
  10^5-report reference size ([`PERFORMANCE.md`](PERFORMANCE.md)).
- Natural-language input quality: the only extractor gate run failed one criterion ([`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md) section 9).
- Negative evidence, rule exceptions in an open world, authorised-dispute semantics, entity merges or open-schema extraction.
- Anything about Settings 2 and 3 of the study, concurrent or multi-process use, multi-tenant isolation, real MCP client
  compatibility, or third-party baselines.
- That an agent behaves better with it: the only agent-level result is symbolic and checks the kernel against hand-written gold.
- A LongMemEval improvement (see the README), or the study's 20-point and latency figures.
- A security audit: the threat model is a design review, not an assessment.

## Optional: SBOM (CycloneDX)

The runtime has no dependencies, so the SBOM is mostly the package itself plus the optional extras. To produce one for a release:

```bash
pip install cyclonedx-bom
# Runtime only (expected to list palimem alone):
python -m venv /tmp/sbom-venv && /tmp/sbom-venv/bin/pip install .
cyclonedx-py environment /tmp/sbom-venv --output-format JSON --output-file sbom-runtime.cdx.json
# With an extra, for example Bedrock:
/tmp/sbom-venv/bin/pip install ".[bedrock]"
cyclonedx-py environment /tmp/sbom-venv --output-format JSON --output-file sbom-bedrock.cdx.json
```

Attach the files to the GitHub release. This is an optional step and not part of `release.yml`; if it is made automatic later, add it as a job that uploads the SBOMs as release assets and keep `cyclonedx-bom` out of the project's own dependencies. Check the exact command line against the installed `cyclonedx-bom` version, since its CLI has changed between major versions.

## What never to do

- Do not upload from a laptop with an API token once trusted publishing is configured.
- Do not tag from a branch other than `main`.
- Do not publish a version whose changelog entry or README status table claims something the code does not do.
