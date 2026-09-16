"""Workflow contract: what ci.yml and release.yml must keep doing.

  * `wheel` must build both distributions and upload `dist/`: building
    `--wheel` only would leave the sdist the project ships
    (`[tool.hatch.build.targets.sdist]`) unbuilt, and throwing `dist/` away
    would leave the `smoke` job nothing to install.

Read as text with pathlib + re, not PyYAML: this project does not declare a
yaml dependency, and YAML 1.1 parses the bare `on:` key as the boolean
`True`, which makes structural assertions on it worse than a regex over text.

`.pip-audit-ignore` is exercised here too (test 5) because both scripts/gate.sh
and the `audit` job in this same file extract its entries with the identical
`grep -oE '^(PYSEC|GHSA)[A-Za-z0-9-]+'` — an entry shaped so that regex cannot
see it is a vulnerability silently un-ignored.

  * Every third-party `uses:` reference must be pinned to a commit hash, not
    a mutable tag, so that what CI executes cannot change without a commit
    here.

  * `release.yml` must check the tag against the declared version before it
    builds, and must keep the publishing identity out of the job that checks
    this repository out.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
PIP_AUDIT_IGNORE = REPO_ROOT / ".pip-audit-ignore"
PYPROJECT = REPO_ROOT / "pyproject.toml"

CI_TEXT = CI_WORKFLOW.read_text(encoding="utf-8")
RELEASE_TEXT = RELEASE_WORKFLOW.read_text(encoding="utf-8")
WORKFLOW_TEXT = {CI_WORKFLOW: CI_TEXT, RELEASE_WORKFLOW: RELEASE_TEXT}


def _job_text(workflow_path: Path, name: str) -> str:
    """Slice a top-level job's text out of a workflow, from `^  <name>:$` up to
    the next top-level job key (`^  \\w[\\w-]*:$`) or end of file, so
    assertions scoped to one job cannot be satisfied by a setting that lives
    in another job."""
    workflow_text = WORKFLOW_TEXT[workflow_path]
    match = re.search(rf"^  {re.escape(name)}:$", workflow_text, re.MULTILINE)
    assert match is not None, f"{workflow_path}: no `  {name}:` job found"
    start = match.end()
    next_job = re.search(r"^  \w[\w-]*:$", workflow_text[start:], re.MULTILINE)
    end = start + next_job.start() if next_job else len(workflow_text)
    return workflow_text[start:end]


def test_gate_job_checks_out_full_history() -> None:
    """Diff coverage compares the diff against the pull request's base
    branch; a shallow clone has no `origin/<base>` to compare against, so the
    step fails loudly at best and silently reports a perfect score at worst.
    This setting was previously attributed to a now-deleted external-analysis
    step and was deleted with it — this test is what stops that happening
    again."""
    gate_text = _job_text(CI_WORKFLOW, "gate")
    assert "fetch-depth: 0" in gate_text, (
        f"{CI_WORKFLOW}: `gate` job checkout has no `fetch-depth: 0` — "
        "diff-cover has no origin/<base> to compare against"
    )


def test_diff_coverage_step_is_guarded_on_a_succeeded_suite_and_a_pull_request() -> None:
    """Unlike its neighbours this step must not run after a failed suite —
    there is nothing to report, reporting anyway is how the previous
    new-code number got pinned at zero, and a collection error leaves no
    coverage.xml so a permissive guard adds a missing-file error on top of
    the real failure."""
    gate_text = _job_text(CI_WORKFLOW, "gate")
    assert "diff-cover" in gate_text, f"{CI_WORKFLOW}: `gate` job has no `diff-cover` invocation"
    idx = gate_text.index("diff-cover")
    block_start = gate_text.rfind("\n      - ", 0, idx) + 1
    next_step = re.search(r"^      - ", gate_text[idx:], re.MULTILINE)
    block_end = idx + next_step.start() if next_step else len(gate_text)
    step_text = gate_text[block_start:block_end]

    if_match = re.search(r"^\s*if:.*$", step_text, re.MULTILINE)
    assert if_match is not None, f"{CI_WORKFLOW}: diff-cover step has no `if:` guard"
    if_line = if_match.group(0)
    assert "success()" in if_line, (
        f"{CI_WORKFLOW}: diff-cover step's `if:` does not contain `success()` — "
        f"got: {if_line.strip()!r}"
    )
    assert "github.event_name == 'pull_request'" in if_line, (
        f"{CI_WORKFLOW}: diff-cover step's `if:` does not contain "
        f"`github.event_name == 'pull_request'` — got: {if_line.strip()!r}"
    )
    assert "!cancelled()" not in if_line, (
        f"{CI_WORKFLOW}: diff-cover step's `if:` uses `!cancelled()`, which "
        f"runs it after a failed suite with no coverage.xml — got: {if_line.strip()!r}"
    )


def test_diff_coverage_threshold_is_a_distinct_constant_from_the_repo_floor() -> None:
    """The two thresholds are never expressed in terms of each other, so
    ratcheting one cannot silently move the other."""
    gate_text = _job_text(CI_WORKFLOW, "gate")
    repo_floor_match = re.search(r"--cov-fail-under=(\d+)", gate_text)
    diff_threshold_match = re.search(r'DIFF_COVERAGE_MIN:\s*"?(\d+)"?', gate_text)
    assert repo_floor_match is not None, (
        f"{CI_WORKFLOW}: no `--cov-fail-under=<N>` literal found in the gate job"
    )
    assert diff_threshold_match is not None, (
        f"{CI_WORKFLOW}: no `DIFF_COVERAGE_MIN: <N>` literal found in the gate job"
    )
    assert repo_floor_match.group(1) != diff_threshold_match.group(1), (
        f"{CI_WORKFLOW}: diff-coverage threshold "
        f"({diff_threshold_match.group(1)!r}) equals the whole-repo floor "
        f"({repo_floor_match.group(1)!r}) — they must be distinct literals"
    )

    idx = gate_text.index("diff-cover")
    block_start = gate_text.rfind("\n      - ", 0, idx) + 1
    next_step = re.search(r"^      - ", gate_text[idx:], re.MULTILINE)
    block_end = idx + next_step.start() if next_step else len(gate_text)
    step_text = gate_text[block_start:block_end]
    assert "cov-fail-under" not in step_text, (
        f"{CI_WORKFLOW}: diff-cover step references `cov-fail-under` — the "
        "diff-coverage threshold must not be expressed in terms of the "
        "whole-repo floor"
    )


def test_main_runs_are_never_cancelled() -> None:
    """`cancel-in-progress` must exempt main: a main run superseded by another
    push is what proves the merged tree green, and losing it to cancellation
    loses that proof along with the built distribution."""
    match = re.search(r"^\s*cancel-in-progress:.*$", CI_TEXT, re.MULTILINE)
    assert match is not None, f"{CI_WORKFLOW}: no `cancel-in-progress:` line found"
    line = match.group(0)
    assert "refs/heads/main" in line, (
        f"{CI_WORKFLOW}: cancel-in-progress lost its main exemption — got: {line.strip()!r}"
    )


def test_wheel_job_produces_both_distributions() -> None:
    """The `wheel` job must build and upload both the wheel and the sdist the
    project ships (`[tool.uv.build-backend]`), not just build a wheel and
    throw `dist/` away — otherwise the `smoke` job has nothing to install and
    the sdist is never built at all. It must build with `--no-sources`, the
    way `release.yml` does, or the merge gate proves an artifact that only
    this workspace's source overrides can reproduce."""
    assert not re.search(r"uv build --wheel\b", CI_TEXT), (
        f"{CI_WORKFLOW}: still runs `uv build --wheel` — the wheel-only build "
        "this job must be replaced by `uv build --no-sources` (wheel + sdist)"
    )
    build_run = re.search(r"^\s*run: uv build\b.*$", CI_TEXT, re.MULTILINE)
    assert build_run is not None and "--no-sources" in build_run.group(0), (
        f"{CI_WORKFLOW}: no `uv build --no-sources` invocation found — the "
        "merge gate must build both the wheel and the sdist, and build them "
        "the way `release.yml` does"
    )
    assert "dist/*.tar.gz" in CI_TEXT or ".tar.gz" in CI_TEXT, (
        f"{CI_WORKFLOW}: the assertion step never checks the sdist "
        "(no `dist/*.tar.gz` / `.tar.gz` reference)"
    )
    assert "actions/upload-artifact" in CI_TEXT, (
        f"{CI_WORKFLOW}: `wheel` job never uploads `dist/` — "
        "no `actions/upload-artifact` step found"
    )
    assert re.search(r"path:.*dist", CI_TEXT), (
        f"{CI_WORKFLOW}: no `path:` entry pointing at `dist` for the upload-artifact step"
    )


def test_vulnerability_ledger_exists_and_is_machine_readable() -> None:
    """`.pip-audit-ignore` must exist and every entry's first token must match
    the exact shape `scripts/gate.sh` and the `audit` job extract with
    `grep -oE '^(PYSEC|GHSA)[A-Za-z0-9-]+'` — an entry that grep cannot see is
    a vulnerability silently un-ignored, and an empty ledger drowns the real
    signal in a huge audit diff."""
    assert PIP_AUDIT_IGNORE.is_file(), (
        f"{PIP_AUDIT_IGNORE}: missing — scripts/gate.sh and the `audit` job both fail without it"
    )
    entry_pattern = re.compile(r"^(PYSEC|GHSA)[A-Za-z0-9-]+$")
    entries = 0
    for lineno, raw_line in enumerate(
        PIP_AUDIT_IGNORE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        first_token = stripped.split()[0]
        assert entry_pattern.match(first_token), (
            f"{PIP_AUDIT_IGNORE}:{lineno}: {first_token!r} does not match "
            "^(PYSEC|GHSA)[A-Za-z0-9-]+$ — the grep both scripts/gate.sh and "
            "ci.yml use to build --ignore-vuln flags cannot see this entry"
        )
        entries += 1
    assert entries > 0, (
        f"{PIP_AUDIT_IGNORE}: no advisory entries found — auditing with an "
        "empty ignore list drowns the real signal"
    )


@pytest.mark.parametrize(
    "workflow_path", [CI_WORKFLOW, RELEASE_WORKFLOW], ids=lambda path: path.name
)
def test_third_party_actions_are_pinned_to_a_commit_hash(workflow_path: Path) -> None:
    """A mutable tag (`@v7`, `@v10.0.1`, ...) can be repointed by the action's
    owner at any time, so what CI actually executes could change with no
    commit in this repository. Every `uses:` reference carries a full hash
    plus a trailing `# <tag>` comment so the pin stays readable."""
    workflow_text = WORKFLOW_TEXT[workflow_path]
    hash_pattern = re.compile(r"^[a-f0-9]{40}$")
    checked = 0
    for match in re.finditer(r"^\s*(?:- )?uses:\s*(\S+)(.*)$", workflow_text, re.MULTILINE):
        reference, rest_of_line = match.group(1), match.group(2)
        if reference.startswith("./"):
            continue  # local workflow reuse, e.g. `uses: ./.github/workflows/ci.yml` — no hash to pin
        checked += 1
        owner_repo, _, pin = reference.partition("@")
        assert hash_pattern.match(pin), (
            f"{workflow_path}: `uses: {reference}` is not pinned to a full "
            "40-character commit hash — a mutable tag can be repointed by "
            f"{owner_repo}'s owner at any time, changing what CI executes "
            "with no commit in this repository"
        )
        assert re.search(r"#\s*\S+", rest_of_line), (
            f"{workflow_path}: `uses: {reference}` has no trailing `# <version>` "
            "comment — the pinned hash is unreadable by a human without one"
        )
    assert checked > 0, (
        f"{workflow_path}: no third-party `uses:` references were checked — "
        "the matching regex is silently matching nothing"
    )


def test_release_publishes_only_what_it_proved() -> None:
    """The release workflow's value is the order of its steps and the split
    between its jobs, and a linter can see neither.

    A version is spent the moment it reaches the index: it can be yanked,
    never reused. So the tag has to be checked against the declared version
    *before* `dist/` exists, and the credential that can publish has to live
    in a job that never sees this repository's source — otherwise a
    compromised build step has something to spend. Both properties survive
    `actionlint` and `zizmor` unscathed; this test is what holds them.
    """
    for tag_pattern in (
        '"v[0-9]+.[0-9]+.[0-9]+"',
        '"v[0-9]+.[0-9]+.[0-9]+rc[0-9]+"',
        '"v[0-9]+.[0-9]+.[0-9]+[ab][0-9]+"',
    ):
        assert tag_pattern in RELEASE_TEXT, (
            f"{RELEASE_WORKFLOW}: no `{tag_pattern}` tag trigger — a release must "
            "start for every version shape the version tool emits"
        )

    build_text = _job_text(RELEASE_WORKFLOW, "build")
    publish_text = _job_text(RELEASE_WORKFLOW, "publish-pypi")

    assert "needs: build" in publish_text, (
        f"{RELEASE_WORKFLOW}: `publish-pypi` has no `needs: build` — it would "
        "publish without the build job having proven anything"
    )
    assert "id-token: write" in publish_text, (
        f"{RELEASE_WORKFLOW}: `publish-pypi` does not request `id-token: write` — "
        "there is no trusted-publisher token to exchange without it"
    )
    assert "id-token" not in build_text, (
        f"{RELEASE_WORKFLOW}: the `build` job requests `id-token` — the job that "
        "checks this repository out must hold no credential that can publish"
    )

    version_guard = re.search(r"^\s*(?:run: )?.*uv version --short", build_text, re.MULTILINE)
    build_step = re.search(r"^\s*(?:run: )?.*uv build\b", build_text, re.MULTILINE)
    assert version_guard is not None, (
        f"{RELEASE_WORKFLOW}: the `build` job never reads the declared version "
        "(`uv version --short`), so it cannot check the tag against it"
    )
    assert build_step is not None, f"{RELEASE_WORKFLOW}: the `build` job runs no `uv build`"
    assert "github.ref_name" in build_text, (
        f"{RELEASE_WORKFLOW}: the `build` job never reads `github.ref_name`, so the "
        "tag it is releasing is never compared to anything"
    )
    assert version_guard.start() < build_step.start(), (
        f"{RELEASE_WORKFLOW}: the `build` job checks the tag against the declared "
        "version after building — once dist/ exists the mistake is one step from "
        "the index"
    )
    assert re.search(r"uv build.*--no-sources", build_text), (
        f"{RELEASE_WORKFLOW}: `uv build` runs without `--no-sources`, so the published "
        "artifact is built the way this workspace resolves rather than the way anyone "
        "else would build it"
    )

    for distribution in ("dist/*.whl", "dist/*.tar.gz"):
        assert re.search(
            rf"--isolated --no-project --with {re.escape(distribution)}.*smoke_test\.py",
            build_text,
        ), (
            f"{RELEASE_WORKFLOW}: nothing runs the smoke test against `{distribution}` in "
            "isolation — FR-015 requires `--isolated --no-project` on the same line as "
            "`--with dist/*` or the source tree checked out in this job leaks into the "
            "smoke test"
        )


def test_ci_runs_the_service_free_gate() -> None:
    """The fork's gate needs no infrastructure to run: five runtime
    dependencies, no graph server, no baked models, no `evaluation` package.

    So ci.yml must provision none of it — a leftover ArcadeDB service
    container, model-bake step or `evaluation` path argument is a job that
    cannot pass on this tree, and a green-looking one that silently still
    pays for infrastructure the package no longer has. The positive half of
    the assertion is what stops "delete everything" from satisfying it: the
    five checks scripts/gate.sh runs must all still be here."""
    assert not re.search(r"^\s*services:\s*$", CI_TEXT, re.MULTILINE), (
        f"{CI_WORKFLOW}: still declares a `services:` block — the gate must "
        "run with no service container"
    )
    assert "arcadedb" not in CI_TEXT.lower(), (
        f"{CI_WORKFLOW}: still references ArcadeDB — the fork has no graph server"
    )
    assert "bake_models" not in CI_TEXT, (
        f"{CI_WORKFLOW}: still bakes models — the fork loads no model weights"
    )
    assert "spacy" not in CI_TEXT.lower(), (
        f"{CI_WORKFLOW}: still installs a spaCy model — the fork has no spaCy dependency"
    )
    assert not re.search(r"(?<![\w/.-])evaluation(?![\w/.-])", CI_TEXT), (
        f"{CI_WORKFLOW}: still names the `evaluation` target, which this tree does not ship"
    )

    gate_text = _job_text(CI_WORKFLOW, "gate")
    for check in (
        "ruff check",
        "ruff format",
        "mypy processrecall",
        "lint-imports",
        "bandit -r processrecall",
        "--cov-fail-under=",
    ):
        assert check in gate_text, (
            f"{CI_WORKFLOW}: the `gate` job no longer runs `{check}` — the "
            "service-free gate drops infrastructure, not checks"
        )


def test_smoke_job_declares_the_interpreters_the_classifiers_name() -> None:
    """`requires-python = ">=3.11"` and the classifier list advertise which
    interpreters this package supports; the `smoke` job is what actually
    installs and imports the built wheel on each one. If the two lists
    diverge, either CI is silently skipping an advertised interpreter or the
    classifiers are advertising one nothing ever tests."""
    smoke_text = _job_text(CI_WORKFLOW, "smoke")

    python_version_line = re.search(r"^\s*python-version:.*$", smoke_text, re.MULTILINE)
    assert python_version_line is not None, (
        f"{CI_WORKFLOW}: `smoke` job has no `python-version:` matrix line"
    )
    smoke_versions = set(re.findall(r'"(\d+\.\d+)"', python_version_line.group(0)))

    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    classifiers = pyproject["project"]["classifiers"]
    classifier_versions = {
        version
        for classifier in classifiers
        for version in re.findall(r"Programming Language :: Python :: (\d+\.\d+)", classifier)
    }

    assert smoke_versions and classifier_versions, (
        f"{CI_WORKFLOW} smoke matrix versions {smoke_versions!r} and "
        f"{PYPROJECT} classifier versions {classifier_versions!r}: neither may be empty"
    )
    assert smoke_versions == classifier_versions, (
        f"{CI_WORKFLOW} smoke matrix declares {sorted(smoke_versions)} but "
        f"{PYPROJECT} classifiers advertise {sorted(classifier_versions)} — "
        "the interpreters CI tests and the interpreters the package claims to "
        "support must be the same set"
    )


def test_the_plugin_declaration_is_exercised_on_windows() -> None:
    """The gap that shipped a tool server nobody could start.

    Every other job here is `ubuntu-latest`, and the plugin's launch
    declarations are the one part of this repo whose correctness is decided by
    the operating system: `.claude-plugin/mcp.json` named `sh`, which Linux
    resolves and Windows does not, so the suite was green on every runner while
    the server failed to spawn on every Windows install. A declaration test
    that never runs on Windows cannot see that, however it is written — the
    missing runner is the defect, not the assertion.

    Scoped to the declaration and packaging tests rather than the whole suite:
    this buys the one property Linux cannot prove, and nothing else.
    """
    plugin_text = _job_text(CI_WORKFLOW, "plugin")
    assert "windows-latest" in plugin_text, (
        f"{CI_WORKFLOW}: the `plugin` job does not run on windows-latest — the "
        "launch declarations are exactly what a Linux-only matrix cannot check"
    )
    assert "ubuntu-latest" in plugin_text, (
        f"{CI_WORKFLOW}: the `plugin` job dropped ubuntu-latest — Windows is the "
        "addition, not the replacement"
    )
    assert "test_mcp_declaration.py" in plugin_text, (
        f"{CI_WORKFLOW}: the `plugin` job does not run the MCP declaration tests"
    )
    assert "test_hooks_declaration.py" in plugin_text, (
        f"{CI_WORKFLOW}: the `plugin` job does not run the hooks declaration tests"
    )
    assert "-m" in plugin_text and "slow" in plugin_text, (
        f"{CI_WORKFLOW}: the `plugin` job must select the `slow` marker — the "
        "live handshake is the only check that actually spawns the server"
    )
