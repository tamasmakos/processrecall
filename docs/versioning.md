# Versioning & deprecation policy

processrecall follows [Semantic Versioning](https://semver.org). From 1.0.0 the
version is single-sourced from `processrecall/_version.py` (`__version__`) — the
file `[tool.hatch.version]` in `pyproject.toml` points the build backend at,
and the only place the version is written by hand. `processrecall/__init__.py`
merely re-exports it.

## What "public" means

Semver applies to the **documented public surface** only:

- the names in `processrecall.__all__` (the package root);
- the four tools of the MCP tool contract;
- the `processrecall-mcp` entry point and the `python -m processrecall.cli`
  commands;
- the hook verbs `hooks/hooks.json` invokes.

Everything else — the modules behind those seams, the snapshot layout and the
episodic index schema — is implementation detail and may change in any release.

## Deprecation

A deprecated public name keeps working for **at least one minor release** and
emits a `DeprecationWarning` pointing at the replacement, then is removed in the
next major. There are no current deprecations.

The plugin's own version lives in `.claude-plugin/plugin.json` and must be
bumped with it: the bootstrap's ready marker holds that version, and a bump is
the whole of the upgrade path for an installed plugin.

## Releasing

1. Bump `__version__` in `processrecall/_version.py` and the `version` in
   `.claude-plugin/plugin.json`.
2. `make ci` (the pre-push gate — ruff, `mypy --strict`, import-linter, bandit,
   unit tests at the coverage floor, dependency audit — plus wheel size + py.typed).
3. Commit, then `git tag vX.Y.Z` and push the tag: `git push origin vX.Y.Z`.

Pushing the tag is what triggers `.github/workflows/release.yml` — the rest is
automated. It reruns the full CI gate (`ci.yml`, called by reference), then
asserts that the pushed tag
matches the `__version__` literal in `processrecall/_version.py`. If they
disagree, the run fails before any upload, reporting both values. Only then
does it attach the wheel that CI itself built — no separate build step, no
hand-run `uv build` from a laptop.

### Where a release goes

**Nowhere public.** The wheel is attached as an asset on this private
repository's GitHub Release for the pushed tag; it is not published to PyPI or
any other public index. The Release is the private channel, and access to the
repository is what gates access to the artifact.

Two assets are attached:

| Asset | What it is |
| --- | --- |
| `processrecall-<version>-py3-none-any.whl` | the library, the same bytes CI tested |
| `processrecall-deploy-<version>.tar.gz` | the deployment bundle: `deploy/` and `scripts/` beside the wheel, in the repository's own layout, so `deploy/Dockerfile` builds identically from the unpacked bundle or from a checkout |

The bundle exists because a recipient must be able to build the deployment
without access to this repository. `scripts/` ships in no wheel, so the bundle
carries it rather than the client cloning for three files.

There is no one-time registration step. Attaching a Release asset needs only
`contents: write`, scoped to the publish job, and no stored token: earlier
releases used PyPI's Trusted Publisher over OIDC, and that machinery is gone
along with the public index it served.

### Recovering from a failed release

A pushed tag is not cleanly re-pushable. Recover **fix-forward**: bump to the
next version and tag again. Never delete and re-push a
tag — it is not recoverable the way a normal bump-and-retag is.

Re-running the upload for the *same* tag is safe (assets are attached with
`--clobber`), which is the one thing that got easier: a public index would have
refused a re-upload of a filename it had already seen.
