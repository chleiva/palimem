# Releasing palimem

Status (2026-10-05): the one-time setup below is **done** (see the table that follows it) and 0.1.0 is **prepared but not released**: the version in the repository is 0.1.0, the release-candidate audit was repeated on it, and the only published artefact is still the 0.0.1 name reservation. `release.yml` has never run. The pipeline is `.github/workflows/release.yml`. Nothing in this repository publishes anything by itself except a **published GitHub release**, and publishing uses PyPI Trusted Publishing (OIDC), so no API token is stored in GitHub or in the repository.

## One-time setup (the maintainer does this by hand)

**State on 2026-10-05.** Items marked *API* were read back through the GitHub API; the others are the maintainer's report
(neither PyPI nor TestPyPI can be inspected from the repository).

| Item | State | Checked |
|---|---|---|
| PyPI trusted publisher for `chleiva/palimem`, workflow `release.yml`, environment `pypi` | done | maintainer |
| Account-wide PyPI token used for the 0.0.1 reservation | revoked | maintainer |
| TestPyPI pending publisher for project `palimem`, same repository and workflow, environment `testpypi` | done | maintainer |
| GitHub environment `pypi` with the maintainer as required reviewer | done | API |
| GitHub environment `testpypi` | done | API |
| Private vulnerability reporting | on | API |
| Dependabot alerts and security updates, secret scanning | on | API |
| Code scanning (the CodeQL job of `security.yml`) | running | workflow runs |
| Branch protection on `main`: `harness`, `test (3.11)`, `test (3.12)`, `test (3.13)` required; no pull request required; administrator bypass on | done | API |

The sections below stay as the reference for how each item is configured.

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
6. **Dry run** (required for the first release, by the author's ruling of 2026-10-05: **0.1.0 goes to TestPyPI first**): Actions > `release` > *Run workflow*. Manual runs publish to **TestPyPI only**. TestPyPI keeps every version number it has received and the pending publisher creates the project on the first upload, so run this with the version you mean to release (0.1.0), not a throwaway. Check the result with `pip install --index-url https://test.pypi.org/simple/ --no-deps palimem==<version>` in a clean environment.
7. **Tag and release**, **only after** the README gate section matches `docs/GATES.md` (`tests/test_gates_alignment.py` checks this and runs in CI) and the TestPyPI dry run succeeded: create the tag `v<version>` (it must equal `v` plus the `pyproject.toml` version; the workflow fails otherwise) and publish a GitHub release for it. Publishing the release triggers the real PyPI upload, after the environment approval.
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
4. **`py.typed` (fixed after this audit):** the first audit found no `py.typed` marker; the marker is now in `src/palimem/` and
   in the wheel (re-checked in the 0.1.0 preparation below).
5. **Not verified:** the installed wheel was exercised on Python 3.13 only (the machine's interpreter); the test suite runs
   on 3.11, 3.12 and 3.13 in CI, but a wheel install on 3.11 and 3.12 has not been run. The `release.yml` workflow has not
   been executed: the trusted publishers are now configured, but no release or TestPyPI upload has been made.

## What 0.1.0 does and does not promise

This is the text to hold the first real release to; it changes nothing by itself. The numbering is the author's: 0.1.0 is the
first public release and spans what the earlier plan spread over 0.1 to 0.4 ([`VERSIONING.md`](VERSIONING.md) section 4).
Where each gate stands is in [`GATES.md`](GATES.md), and the README gate section must match it before PyPI.

**It promises**

- `pip install palimem` installs with no dependencies on Python 3.11 or newer, and the README quickstart, the agent tool
  API, the MCP server over stdio and the `palimem` CLI work as documented in [`AGENT_GUIDE.md`](AGENT_GUIDE.md).
- The full pipeline answers all 30,272 queries of the 500 frozen Setting 1 streams with 0 disagreements against the study's
  frozen gold on status, value, alternatives and (through the compat projection) provenance, and this is checked in CI
  ([`PIPELINE.md`](PIPELINE.md), [`HARNESS.md`](HARNESS.md)); the study's cached claims for Settings 2 and 3 replay with 0
  disagreements against the study's own store, one Setting 3 stream excepted ([`SETTINGS23.md`](SETTINGS23.md)).
- The log is append-only and hash-chained, appends are transactional and idempotent, `verify_log` detects edits, deletions and
  reordering, and an erasure leaves a tombstone that keeps the chain intact ([`STORAGE.md`](STORAGE.md)).
- An LLM reaching palimem through the agent tool API cannot choose source, origin or actor, cannot retract external evidence
  and cannot dispute without a host grant (19 of 20 trust-boundary fixtures pass; the other has a recorded cause).
- Stored data written by one 0.x release stays readable by migration in the next ([`VERSIONING.md`](VERSIONING.md) section 4).

**It does not promise**

- A stable contract: any `0.MINOR` may break it, marked **BREAKING** in the changelog with a migration note.
- The performance targets: four of the seven declared targets (T2, T3, T6, T7) are missed at the sizes reached, and no run
  reached the 10^5-report reference size ([`PERFORMANCE.md`](PERFORMANCE.md)).
- Natural-language input quality: the only extractor gate run failed one criterion (`gpt-oss-20b`, dropped change cues 0.235
  against 0.20, [`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md) section 9); the Ministral models were not run on the
  test split.
- Rule exceptions (reserved in 0.x), a source-scope withdraw (an admission operation exists instead), open-schema extraction,
  an embedding or LLM entity resolver, or learning of any kind.
- Concurrent or multi-process use, multi-tenant isolation, real MCP client compatibility, or a comparison with any
  third-party memory system (none was run).
- That an agent behaves better with it in general: the agent-level evidence is a pilot for **a compliant reader of the
  kernel's text** (the `recall` text carries its own decision instructions), exploratory, with a model third opinion as the
  second annotation and no human annotation.
- A LongMemEval improvement (see the README), or the study's 20-point and latency figures.
- A security audit: the threat model is a design review, not an assessment, and the poisoning gate has never been run.

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
