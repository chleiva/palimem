# Releasing palimem

Status: draft (T-K1). The pipeline is `.github/workflows/release.yml`. Nothing in this repository publishes anything by itself except a **published GitHub release**, and publishing uses PyPI Trusted Publishing (OIDC), so no API token is stored in GitHub or in the repository.

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
4. **Gates locally**: `ruff check .`, `mypy --strict src/palimem`, `python -m palimem.schemas --check`, `pytest -q`, `python -m harness.differential` (full run), and the conformance suite.
5. **Merge to `main`** and wait for CI (including the `harness` job) and the `security` workflow to be green.
6. **Dry run** (optional but recommended for the first release of each minor): Actions > `release` > *Run workflow*. Manual runs publish to **TestPyPI only**. Check the result with `pip install --index-url https://test.pypi.org/simple/ --no-deps palimem==<version>` in a clean environment.
7. **Tag and release**: create the tag `v<version>` (it must equal `v` plus the `pyproject.toml` version; the workflow fails otherwise) and publish a GitHub release for it. Publishing the release triggers the real PyPI upload, after the environment approval.
8. **Verify**: `pip install palimem==<version>` in a clean environment, `python -c "import palimem; print(palimem.__version__)"`, and check the project page.
9. **Announce** only what the release contains; the README status table must match.

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
