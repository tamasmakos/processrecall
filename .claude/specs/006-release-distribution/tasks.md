# Tasks: Release and Distribution — PyPI, MCP Registry, and a Plugin That Installs the Release

**Feature**: `006-release-distribution` | **Branch**: `006-release-distribution`

**Input**: [spec.md](./spec.md), [research.md](./research.md)

**Implementation tree**: this repository. Every path below is relative to its repository root,
which is simultaneously the plugin and the Python package. Commands run at the repository root
on the host; this tree ships no container.

Each task carries one `Verify` sub-bullet: a single command that **fails on the tree as it
stands and passes when the task is done**. Where a task introduces the check itself, writing
that check is part of the task, and "fails first" means it fails for the stated reason rather
than on a missing file.

`[P]` marks tasks that touch disjoint files and may run in parallel with their siblings.

Phases follow the spec's user-story priorities: Phase 1 and Phase 2 are the foundation every
story needs, Phases 3–6 are the four stories in priority order, Phase 7 is documentation, and
Phase 8 is the owner's manual steps outside the repository.

**Do not fold the uncommitted `scripts/gate.sh` change into any commit here.** It is an
unrelated path-conversion fix (R9).

---

## Phase 1: The distribution builds with the package manager's backend (FR-001..FR-007)

Nothing downstream is trustworthy until the artifact is the one strangers will get. This phase
changes what is built and proves it against the archive, not against configuration.

- [ ] **T001** Rewrite `[build-system]` in `pyproject.toml` to `requires = ["uv_build>=0.12.0,<0.13"]`
  and `build-backend = "uv_build"`; delete `[tool.hatch.build.targets.wheel]` and
  `[tool.hatch.build.targets.sdist]`; add `[tool.uv.build-backend]` with `module-root = ""`
  for this repository's flat layout. Replace the two deleted comment blocks with one that
  records **why** there is nothing to declare any more (R2: the backend ships everything under
  the module root, ignores `.gitignore`, and therefore cannot silently drop a pack — so the
  archive assertion in T002 is now the binding check, not a second opinion). In
  `tests/test_packaging.py`, replace `test_both_packs_are_declared_to_hatchling` with
  `test_the_backend_is_pinned_and_the_flat_layout_is_declared`: assert the backend is
  `uv_build`, that its requirement carries **both** a lower and an upper bound, and that
  `tool.uv.build-backend.module-root == ""`. Update the module docstring, which describes
  hatchling's drop-what-VCS-ignores behaviour.
  - Verify: `uv run pytest -q tests/test_packaging.py::test_the_backend_is_pinned_and_the_flat_layout_is_declared`

- [ ] **T002** Harden the archive assertions in `tests/test_packaging.py`, which are now the only
  thing between an exclusion pattern and an empty pack. Keep
  `test_wheel_under_ceiling_and_ships_both_packs` as it is — it already builds and reads the
  real wheel — and add `test_the_sdist_carries_the_source_and_not_the_workspace`: build the
  source distribution, assert it contains the project manifest and the package source, and
  assert it contains **no** member under `tests/`, `research/` or `.claude/`. Also assert no
  `source-exclude` or `wheel-exclude` pattern in `pyproject.toml` matches a `.json` path.
  - Verify: `uv run pytest -q tests/test_packaging.py::test_the_sdist_carries_the_source_and_not_the_workspace`

- [ ] **T003** [P] Add `processrecall = "processrecall.server.mcp.stdio_server:main"` to
  `[project.scripts]` in `pyproject.toml`, beside the existing `processrecall-mcp`, so the bare
  distribution name starts the memory server with no arguments (FR-006 — this is what makes the
  registry entry in T014 need no `packageArguments`, per R5). Rewrite the stale comment above
  the table, which still narrates task numbers from spec 005. Add
  `test_the_distribution_name_is_a_console_script` to `tests/test_packaging.py`: the entry point
  named exactly `project.name` exists, and its target module and attribute both resolve — reuse
  the `find_spec` / `hasattr` pattern from
  `tests/test_mcp_declaration.py::test_the_server_runs_a_script_that_ships`.
  - Verify: `uv run pytest -q tests/test_packaging.py::test_the_distribution_name_is_a_console_script`

- [X] **T004** [P] Replace `project.description` in `pyproject.toml` with the plugin manifest's
  user-facing sentence, "Procedural graph memory: captures what the agent does, recalls how it
  was done before." (86 characters). The current text still describes GraphKnows — knowledge
  graph ingestion and hybrid retrieval — which this fork does not do. Add
  `test_the_description_fits_the_registry_limit` to `tests/test_packaging.py`: at most 100
  characters (R7), and equal to the description in `.claude-plugin/plugin.json`. Note that
  `tests/test_plugin_manifest.py::test_the_manifest_metadata_matches_pyproject` currently
  asserts the two descriptions are *intentionally distinct*; that assertion is superseded and
  must be rewritten in this task, not deleted (FR-029). (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_packaging.py::test_the_description_fits_the_registry_limit tests/test_plugin_manifest.py`

- [X] **T005** Run `uv lock` so the lock file reflects the backend change, then build and compare: (already satisfied on 006-release-distribution: its Verify passed before any work)
  `uv build` on this tree against the artifacts the previous backend produced, confirming the
  wheel still carries `py.typed`, both pack directories and nothing new, and that the source
  distribution gained no workspace files. Record any difference worth keeping in the T001
  comment. This is a gate, not a code change: stop here and report if the two differ in any way
  T001's comment does not already explain.
  - Verify: `uv run pytest -q tests/test_packaging.py`

---

## Phase 2: One version, derived everywhere (FR-008..FR-011)

The single fact this feature turns on. Written before any publishing exists, so that no channel
is ever added without a test that keeps its version honest.

- [ ] **T006** Create `tests/test_release_versions.py`, marked `unit`, asserting that one
  authoritative version — `project.version` in `pyproject.toml` — equals every derived copy:
  `version` in `.claude-plugin/plugin.json`, `version` and `packages[0].version` in
  `server.json`. Assert also the **plugin pin rule** (FR-008a): the `version` in
  `.claude-plugin/plugin.json` MUST be a stable version — never a pre-release — and MUST equal
  the authoritative version whenever the authoritative version is itself stable. That single
  permitted divergence is what keeps a release candidate off marketplace users. Compare with
  `packaging.version.Version.is_prerelease`, not by string matching. The equality between that
  pin and the `processrecall==<version>` pin in `.claude-plugin/mcp.json` is added by T019,
  because no such pin exists until Phase 5. Assert also that `server.json`'s `name` matches the `mcp-name:`
  marker in `README.md`, that `packages[0].identifier` equals `project.name`, and that
  `packages[0].runtimeHint` is `uvx`. Write it now, against files T007, T014 and T015
  have not created yet: it must fail naming the missing file, and each later task turns one
  failure green. Parse `pyproject.toml` with `tomllib` and the rest as text or JSON — this
  project declares no YAML dependency, the reason given in `tests/test_ci_workflow.py`.
  - Verify: `uv run pytest -q tests/test_release_versions.py` *(expected to fail until T015; it is the running scoreboard for this feature)*

- [X] **T007** Add `"version": "0.1.0"` to `.claude-plugin/plugin.json`, matching
  `pyproject.toml`. Extend `tests/test_plugin_manifest.py` so the manifest's version is read
  from the same source of truth as its author, homepage and licence already are. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_plugin_manifest.py`

- [X] **T008** Create `scripts/sync_version.py`: read the authoritative version and rewrite the
  derived places — both fields in `server.json`, and, **only when the new version is stable**,
  the plugin pin in the plugin manifest and the pin in the server launch declaration once that
  declaration carries one. A pre-release bump MUST leave the plugin pin untouched (FR-010,
  FR-008a) and the script MUST say so in its output rather than silently skipping. It edits JSON as JSON, never by regular expression, and prints what it
  changed. Add `Makefile` targets `bump` (`uv version --bump $(PART)` then the sync — `PART`
  accepts `major`, `minor`, `patch`, `rc`, `alpha`, `beta`, `post`, `dev`, per R3) and
  `version` (`uv version $(V)` then the sync). Update
  `tests/test_repo_hygiene.py::test_scripts_directory_contains_exactly`, which pins the exact
  file set in `scripts/`, to include the new script — that test is deliberate and is updated,
  never relaxed. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_repo_hygiene.py::test_scripts_directory_contains_exactly`

---

## Phase 3 (User Story 1, P1): The release pipeline publishes to the index (FR-012..FR-015)

The MVP slice. When this phase lands, anyone on the internet can install the package.

- [ ] **T009** Create `tests/smoke_test.py` — a plain script, **not** a pytest module. It must not
  be collected by the suite (`testpaths = ["tests"]` with the default `test_*.py` glob would
  collect it; give it no test-shaped functions and guard it under `if __name__ == "__main__"`)
  and must not import `tests/conftest.py`, because it runs under `uv run --isolated
  --no-project` where the source tree is absent. It imports the package, asserts the installed
  version is a valid release identifier, asserts both console entry points resolve via
  `importlib.metadata.entry_points(group="console_scripts")`, and asserts both data pack
  directories are non-empty via `importlib.resources` — the installed location, never a
  repository path.
  - Verify: `uv build && uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py`

- [ ] **T010** Create the `build` job in `.github/workflows/release.yml`. Triggers: `push.tags`
  matching `v[0-9]+.[0-9]+.[0-9]+`, `v[0-9]+.[0-9]+.[0-9]+rc[0-9]+` and
  `v[0-9]+.[0-9]+.[0-9]+[ab][0-9]+` (R3). Top-level `permissions: contents: read`. Steps:
  checkout with `persist-credentials: false`; install the package manager; **assert
  `uv version --short` equals `${GITHUB_REF_NAME#v}` and fail with both numbers named** before
  anything is built (FR-011, and the only affordable guard against spending a version — R1);
  `uv build --no-sources` (FR-005, R3); smoke test the wheel and then the source distribution
  with `uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py` and the `.tar.gz`
  equivalent; upload `dist/` with `if-no-files-found: error`. Every `uses:` pinned to a
  40-character commit hash with a trailing `# vX` comment — `tests/test_ci_workflow.py` enforces
  this and T012 extends it to this file. Route the tag name through `env:` rather than
  interpolating it into a `run:` block, the template-injection rule already applied in `ci.yml`.
  - Verify: `uvx zizmor@1.29.0 --offline .github/workflows/ && uvx --from actionlint-py==1.7.12.24 actionlint .github/workflows/release.yml`

- [X] **T011** Add the `publish-pypi` job to `.github/workflows/release.yml`: `needs: build`,
  `environment: name: pypi`, `permissions: id-token: write` and nothing else, **no checkout**
  (it runs no project code, so it needs none — R3), download the `dist` artifact, generate
  attestations, `uv publish`. No token, no secret, no credential of any kind (FR-012). The job
  split is the security boundary, not a style choice: it is why the publishing identity is
  unreachable from the step that executed this repository's own build. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uvx --from actionlint-py==1.7.12.24 actionlint .github/workflows/release.yml`

- [X] **T012** Extend `tests/test_ci_workflow.py` into a contract over both workflow files. (already satisfied on 006-release-distribution: its Verify passed before any work)
  Parametrize `test_third_party_actions_are_pinned_to_a_commit_hash` over `[ci.yml,
  release.yml]` — it already takes `workflow_path` as a parameter, so this is a change to the
  parameter list. Add `test_release_publishes_only_what_it_proved`: the three tag patterns are
  present; `build` and `publish-pypi` are separate top-level jobs; `id-token: write` appears in
  the publish jobs and **nowhere** in `build`; the build job asserts the tag against the
  declared version before its build step; `--no-sources` is present; and the smoke step runs
  against both `dist/*.whl` and `dist/*.tar.gz`. Reuse the existing `_job_text` slicer so an
  assertion cannot be satisfied by a line in a different job.
  - Verify: `uv run pytest -q tests/test_ci_workflow.py`

- [X] **T013** [P] Change the `wheel` job in `.github/workflows/ci.yml` to `uv build --no-sources`
  so the merge gate builds the way the release builds, and correct the step comment that names
  `[tool.hatch.build.targets.sdist]`, which T001 deleted. Relax
  `tests/test_ci_workflow.py::test_wheel_job_produces_both_distributions` to accept the flag
  while still rejecting a wheel-only build — the property it defends is "both artifacts", not
  the exact command text. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_ci_workflow.py::test_wheel_job_produces_both_distributions`

---

## Phase 4 (User Story 2, P2): The server is listed in the official registry (FR-016..FR-020)

- [ ] **T014** Create `server.json` at the repository root: `$schema` pointing at
  `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`, `name`
  `io.github.tamasmakos/processrecall` (the namespace GitHub authentication grants — R5),
  `description` identical to `project.description`, `repository` with `url` and
  `source: "github"`, `version`, and one `packages` entry: `registryType: "pypi"`,
  `registryBaseUrl: "https://pypi.org"`, `identifier: "processrecall"`, `version`,
  `runtimeHint: "uvx"`, `transport: { "type": "stdio" }`. No `packageArguments` and no
  `runtimeArguments` — T003 made the distribution name runnable, which is the precondition for
  omitting them.
  - Verify: `uv run pytest -q tests/test_release_versions.py`

- [ ] **T015** [P] Add `<!-- mcp-name: io.github.tamasmakos/processrecall -->` to `README.md` on
  its own line directly below the title. It must be followed by a newline and carry no trailing
  punctuation, or the registry's matcher fails (R5). The readme is the published description,
  and descriptions are captured at publication, so this must be in the tagged commit — a marker
  on the default branch alone verifies nothing.
  - Verify: `uv run pytest -q tests/test_release_versions.py`

- [X] **T016** Add a registry-entry validation step to the `plugin` job in
  `.github/workflows/ci.yml`, on the Ubuntu leg only: download `mcp-publisher` at a **pinned**
  release version (not `latest` — R6, same reasoning as the action hash pins) and run
  `mcp-publisher validate`. This is the step that catches an over-length description or a
  name/marker mismatch while the change is still a proposal (SC-005). Extend
  `tests/test_ci_workflow.py` to assert the step exists and that the downloaded version is
  pinned rather than floating. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_ci_workflow.py`

- [X] **T017** Add the `publish-mcp-registry` job to `.github/workflows/release.yml`: (already satisfied on 006-release-distribution: its Verify passed before any work)
  `needs: publish-pypi` (the registry verifies ownership against the *published* description,
  so it cannot run earlier), `permissions: id-token: write` and `contents: read`, checkout,
  pinned `mcp-publisher` download, `mcp-publisher validate`, `mcp-publisher login github-oidc`,
  `mcp-publisher publish`. Wrap the publish in a bounded retry so the index's propagation delay
  cannot fail an otherwise good release (FR-019) — retry the publish, never the PyPI upload.
  The job MUST also be re-runnable on its own against an already-published version (FR-019a):
  it rebuilds and republishes nothing, so a listing that fails after a successful upload is
  recovered by re-running this job, never by cutting a new version. Its failure leaves the run
  red so the missing listing is visible.
  - Verify: `uv run pytest -q tests/test_ci_workflow.py && uvx zizmor@1.29.0 --offline .github/workflows/`

---

## Phase 5 (User Story 3, P3): The installed plugin runs the released version (FR-021..FR-026)

> **Gated on a published stable version.** Nothing in Phase 5 or Phase 6 may be merged until
> `0.1.0` is on the index (T031). The pin these tasks introduce must be a stable version
> (FR-008a), the first release is deliberately a release candidate (T029), and pinning a version
> that does not exist would ship a plugin that cannot start a server. Until the gate opens the
> plugin keeps its current checkout behaviour, unchanged and not partially rewritten (FR-021a).
> Phases 1–4 are complete and shippable without this.

The largest rewrite in the feature, and the one with a real ordering trap: **T019 must land with
T018**, or the suite is red with no honest way to make it green.

- [ ] **T018** Rewrite `.claude-plugin/mcp.json` to launch the release:
  `command: "uvx"`, `args: ["processrecall==<version>"]`, `type: "stdio"`, and **no `env`
  block** — there is no project environment to point at any more. This retires `--project`,
  `--frozen`, `--no-dev` and `UV_PROJECT_ENVIRONMENT` from the declaration, and with them the
  first-session race the old design worked around: `uvx` provisions its own cached environment.
  - Verify: `uv run pytest -q tests/test_release_versions.py`

- [X] **T019** Rewrite `tests/test_mcp_declaration.py` to pin the new invariants (FR-029 — the
  superseded assertions are rewritten, never deleted). Keep every test that is about
  spawnability: the command resolves on `PATH`, names no shell, leaves `${...}` placeholders
  unexpanded. Replace the project/lock/environment tests with: the command is `uvx`; there is
  exactly one argument, of the form `processrecall==<version>`; and the distribution it names
  ships a console script of that name. **Then fix the `slow` handshake test**, which spawns the
  declared command for real against a throwaway environment and cannot resolve a version that
  is not yet published (R8). Build the wheel into `tmp_path` with the existing
  `tests/test_packaging.py::_build_wheel` helper and point the resolver at that directory via
  the environment, so the unpublished version resolves locally **while the spawned argument
  vector stays exactly the declared one**. Editing the command inside the test to something
  that resolves would defeat the only check that has ever caught an unspawnable declaration.
  Update the module docstring, whose first-session-race paragraph T018 made moot. Finally,
  extend `tests/test_release_versions.py` with the last assertion it was written without: the
  pin inside the launch declaration equals the plugin manifest pin exactly (FR-008a). (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q -m "unit or slow" tests/test_mcp_declaration.py`

- [X] **T020** Rewrite `bin/bootstrap.sh` to prepare the environment from the release. The
  readiness key becomes the pinned version read from `.claude-plugin/plugin.json` with a POSIX
  `sed`, replacing the checksum-of-lock-joined-to-root key; a missing or unreadable version
  takes the existing `report`-and-exit-0 path. Preparation becomes `uv venv` followed by
  installing `processrecall==<version>` into it, with no `--project`, no lock file and no
  editable install — which retires the stale-root hazard the old key existed to catch. Keep
  unchanged: the fast path that exits having invoked nothing, the Windows
  `Scripts/python.exe` → `bin/python.exe` mirror the hooks depend on, the `.ready` marker, and
  the rule that every failure leaves as one line of JSON with exit 0 so a `SessionStart` hook
  never surfaces against the developer's own session. Rewrite the header comment, which
  explains the retired key in detail. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/integrations/claude_code/test_bootstrap.py`

- [X] **T021** Rewrite the two bootstrap test files against the new key (FR-029). (already satisfied on 006-release-distribution: its Verify passed before any work)
  In `tests/integrations/claude_code/test_bootstrap.py`: `ready_key` becomes the pinned version;
  `test_a_changed_lock_is_what_makes_the_marker_stale` and
  `test_a_changed_root_is_what_makes_the_marker_stale` become one test that a changed **pin**
  makes the marker stale, plus one asserting a changed root does **not** — that is the hazard
  this task removes, and asserting its absence is what stops it creeping back;
  `test_an_unreadable_lock_is_one_message_and_an_unharmed_session` targets the manifest;
  `test_a_sync_that_fails_is_reported_rather_than_marked_ready` looks for the install command
  and the pin in the message. Keep untouched every test about the fast path, the missing
  package manager, and the platform-specific install hint. In `tests/cli/test_bootstrap.py`,
  update the mirrored `ready_key` helper and the message assertion.
  - Verify: `uv run pytest -q tests/cli/test_bootstrap.py tests/integrations/claude_code/test_bootstrap.py`

- [X] **T022** [P] Correct the docstrings in `processrecall/cli/bootstrap.py` that describe the
  retired behaviour — "the lock it syncs from" on `Installation.root` and the "sync again"
  paragraph on the force path. Behaviour is unchanged: the command still runs `bin/bootstrap.sh`
  so that debugging an install by hand is never debugging a second implementation of it. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/cli/test_bootstrap.py`

---

## Phase 6 (User Story 4, P4): A developer can still work against a checkout (FR-024)

> Gated with Phase 5. The switch is a branch inside the rewritten preparation script.

- [X] **T023** Add the development switch to `bin/bootstrap.sh`: when
  `PROCESSRECALL_PLUGIN_SOURCE=checkout` is set, prepare the environment from the plugin root as
  an editable install instead of from the index, keyed on the same version string. Off by
  default, so the development path can never be what an end user silently gets. Add a test to
  `tests/integrations/claude_code/test_bootstrap.py` for both directions — set, it installs from
  the root; unset, it installs the pin — and name the switch in the readme's development
  section (T024). (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/integrations/claude_code/test_bootstrap.py`

---

## Phase 7: Documentation the change made false (FR-027, FR-028)

- [X] **T024** Extend `## Install` in `README.md` with the direct route — installing the package
  and running it by name — naming **both** console entry points, which
  `tests/test_readme_install.py` requires of this section. Leave the marketplace commands and the
  paragraphs describing plugin preparation alone: Phase 5 has not landed, so they are still true.
  Keep every link absolute: the readme is the published description and relative links resolve
  against the index and 404 there. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_readme_install.py`

- [X] **T024a** Document the recovery route (FR-015a) in `README.md` or `docs/design.md`,
  wherever releasing is described: a broken published version is superseded by a new patch
  version with the plugin pin advanced to it, **and then** withdrawn from the index so new
  installs cannot select it. Withdrawal alone strands every plugin user pinned to that exact
  number. No published version is ever deleted or its number reused. (already satisfied on 006-release-distribution: its Verify passed before any work)
  - Verify: `uv run pytest -q tests/test_docs_shape.py`

- [ ] **T024b** *(gated with Phase 5)* Rewrite the plugin half of `## Install`: replace the
  lock-file sync and `uv run` launch paragraphs with what then happens — the first session
  installs the pinned release once and the memory server runs that release — and document the
  development switch from T023 in the checkout paragraph.
  - Verify: `uv run pytest -q tests/test_readme_install.py`

- [ ] **T025** [P] Correct the sentences in `docs/design.md` and `docs/architecture.md` that the
  backend switch made false, and amend the design record with that decision. Update the
  sentence, not the section: documentation is updated only where the change made it false. The
  `uv sync` description of plugin preparation is **gated with Phase 5** — it is still true until
  that phase lands, and is corrected there alongside T024b.
  - Verify: `uv run pytest -q tests/test_docs_shape.py`

- [ ] **T026** [P] Correct the three docstrings and one assertion message that still narrate the
  retired backend configuration: the module docstring and line-72 message in
  `tests/test_readme_install.py`, and the comment at `tests/test_removal_ledger.py:104`. These
  describe configuration that no longer exists; the properties they assert are unaffected.
  - Verify: `uv run pytest -q tests/test_readme_install.py tests/test_removal_ledger.py`

---

## Phase 8: Owner steps, outside the repository

Not agent work. Sequenced here because the release cannot complete without them, and because
T029 is the only real proof this feature works.

- [ ] **T027** Create the `pypi` deployment environment in the repository settings, matching the
  name declared in T011. A reviewer requirement on it is optional and is the owner's call.

- [ ] **T028** Add a **pending** trusted publisher on the index for project `processrecall`:
  owner `tamasmakos`, repository `processrecall`, workflow `release.yml`, environment `pypi`.
  Pending is the correct kind — the project does not exist on the index until the first
  publication (R1).

- [ ] **T029** Cut the first release as a pre-release, so the pipeline's first exercise cannot
  spend the `0.1.0` number: `make bump PART=rc` (or `make version V=0.1.0rc1`), commit, then
  `git tag -a v0.1.0rc1 -m v0.1.0rc1 && git push --follow-tags`. Then verify, in this order:
  1. all three jobs green;
  2. running the package by name from a machine with no checkout answers a protocol handshake;
  3. the registry returns the server for a search on its name.

  The plugin is **not** part of this verification. `make bump PART=rc` leaves the plugin pin
  untouched by design, so marketplace users keep their current behaviour through this release.

- [ ] **T031** Once the release candidate is proven, cut `0.1.0`: `make version V=0.1.0`,
  commit, tag, push. This is the release that **opens the Phase 5 and Phase 6 gate** — a stable
  version now exists for the plugin to pin. Then land Phases 5 and 6 with T024b and the gated
  half of T025, and verify a fresh plugin install on both a POSIX and a Windows host starts the
  memory server on the first session and prepares its environment exactly once.

- [ ] **T030** *(owner decision, separate change)* Amend Principle VII of
  `.claude/constitution.md`, whose requirement that shipped non-`.py` files "MUST be declared to
  the build backend" has no referent under the new backend (spec, Constitution Alignment). The
  constitution's own amendment procedure requires a single reviewed change touching that file
  alone, so this must **not** ride along with any task above. Until it lands, the repository
  carries a MUST that cannot be satisfied as written.

---

## Dependencies

| Phase | Depends on | Why |
|---|---|---|
| 1 | — | Changes what is built; everything else describes the built thing |
| 2 | 1 | The derived-version test asserts against `project.version` and the new script name |
| 3 | 1, 2 | Publishes the artifact, guarded by the tag-versus-version check |
| 4 | 1, 3 | The registry verifies ownership against the published description |
| 5 | 1, 2, and **T031** | The launch pins a stable version, which does not exist until `0.1.0` is published |
| 6 | 5 | The switch is a branch inside the rewritten preparation script |
| 7 | 1–4 for T024/T024a/T025; 5 and 6 for T024b and the gated half of T025 | Documentation is corrected only once the change has made it false |
| 8 | 1–4 merged | Phases 5–6 land after T031, not before |

**Within Phase 5, T018 and T019 land together.** T018 alone leaves the suite red, and the only
way to make it green without T019 is to edit the handshake test's command — the exact shortcut
R8 identifies as defeating the check.

**T006 is the running scoreboard.** It is written first and expected to fail; T007, T014 and
T015 each turn one of its assertions green, and T019 adds its final assertion once the gate
opens.

**The gate is the spine of the ordering.** Phases 1–4 ship on the release candidate. T031 cuts
the first stable version and only then may Phases 5 and 6 be merged. Nothing between those two
points may partially rewrite the plugin (FR-021a).

## Full-suite gate

Run before proposing any of this for merge, not per task:

```
make gate
```

Plus the two checks the gate does not carry, which this feature's changes reach:

```
uvx zizmor@1.29.0 --offline .github/workflows/
uvx --from actionlint-py==1.7.12.24 actionlint .github/workflows/ci.yml .github/workflows/release.yml
```
