"""Repo-shape contract: pins the post-cleanup state issue #203 describes.

The Dockerfile and its deployment stack are gone entirely (the PyPI wheel is
the product now); what remains pinned here is the rest of that cleanup's
debris: a lock-hash staleness guard for a lock the image never used, an
unpinned SonarQube CLI install and the skip_sonar plumbing threaded through
CI, and pytest.ini/ruff.toml/.devcontainer/Node ignore rules a Python-only
repo does not need.

Read files as TEXT with pathlib + re, not PyYAML: this project declares no
yaml dependency, and YAML 1.1 parses the bare `on:` key as the boolean True,
which makes structural assertions on it worse than a regex over text.
pyproject.toml is parsed with `tomllib` since that one is genuinely TOML.

Tests are grouped by area; each assertion names the file and the property it
pins so a failure is legible without reading this docstring.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_SH = REPO_ROOT / "scripts" / "gate.sh"
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
README = REPO_ROOT / "README.md"
GITIGNORE = REPO_ROOT / ".gitignore"
MAKEFILE = REPO_ROOT / "Makefile"


# ─── scripts/: only what a single-stage build and CI need ──────────────────


def test_scripts_directory_contains_exactly() -> None:
    scripts_dir = REPO_ROOT / "scripts"
    # Only *.py/*.sh: a developer tree can carry __pycache__ or other tool
    # caches this test has no business asserting about.
    entries = {p.name for p in scripts_dir.iterdir() if p.suffix in (".py", ".sh")}
    expected = {
        "bake_models.py",
        "measure_traversals.py",
        "preflight.py",
        "docker-entrypoint.sh",
        "gate.sh",
        "sync_version.py",
    }
    assert entries == expected, (
        f"{scripts_dir}: expected exactly {expected}, found {entries} — "
        "install-hooks.sh and sonar-publish.sh belong to removed workflows, "
        "sync_version.py derives every version from pyproject.toml, and "
        "measure_traversals.py is the published traversal measurement (FR-036), "
        "run by hand and deliberately outside the package and the gate"
    )


class TestGateScript:
    def setup_method(self) -> None:
        assert GATE_SH.is_file(), f"{GATE_SH}: missing"
        self.text = GATE_SH.read_text(encoding="utf-8")

    def test_removed_debris(self) -> None:
        # Only the analysis tool: the worktree-vs-shared-container detection
        # (docker inspect/cygpath/MSYS_NO_PATHCONV/GATE_MARKER) is unrelated
        # to SonarQube removal and must survive — this repo works one git
        # worktree per issue, and the shared container mounts the main repo.
        assert "sonar" not in self.text.lower(), (
            f"{GATE_SH}: still contains 'sonar' — this belonged to the removed analysis tool"
        )

    @pytest.mark.parametrize("tool", ["ruff", "mypy", "lint-imports", "bandit", "pytest"])
    def test_still_invokes(self, tool: str) -> None:
        assert tool in self.text, f"{GATE_SH}: no longer invokes {tool!r}"

    def test_worktree_fallback_inherits_volumes_and_env(self) -> None:
        # The fallback `docker run` fires whenever a push comes from a worktree
        # the shared container does not mount — the normal case here, one
        # worktree per issue. Mounting only the repo tree loses
        # `model_cache:/opt/models`, which backs HF_HOME and NLTK_DATA: every
        # WordNet/embedding test then ERRORs on a cold lookup and the weight
        # download runs until the caller's timeout kills the push. That is what
        # parked #137 and #160 on the same fingerprint against a green main.
        run_block = self.text.split("docker run", 1)
        assert len(run_block) == 2, f"{GATE_SH}: no ephemeral `docker run` fallback left"
        fallback = run_block[1]
        for flag in ("--volumes-from", "--env-file"):
            assert flag in fallback, (
                f"{GATE_SH}: the worktree fallback no longer passes {flag!r}, so it "
                f"runs without the model cache / container environment"
            )

    def test_env_dump_is_never_written_into_the_worktree(self) -> None:
        # The dump is the container's whole environment, API keys included, and a
        # gate killed by a caller's timeout does not reliably run its trap. Under
        # $TMPDIR a survivor is inert; under REPO_ROOT it is one `git add -A`
        # away from being published.
        assert 'GATE_ENV_FILE="$(mktemp)"' in self.text, (
            f"{GATE_SH}: the container env dump must go to mktemp, not the repo tree"
        )
        assert "$REPO_ROOT/.gate-env" not in self.text, (
            f"{GATE_SH}: writes the container env dump (secrets) inside the worktree"
        )


def test_gate_runs_without_services() -> None:
    """The fork's gate needs no infrastructure: no graph server, no baked models.

    So the Makefile must offer no target that provisions any — `infra-up`,
    `infra-down`, `bake` and `eval-smoke` all start or feed something this tree
    does not have, and a target that cannot work is worse than an absent one
    because it reads as a supported path. The positive half is what stops
    "delete everything" from satisfying this: `scripts/gate.sh` must still run
    every check, and the local SonarQube targets — which talk to a container
    nobody's everyday stack starts — must survive the cull.
    """
    makefile = MAKEFILE.read_text(encoding="utf-8")
    for target in ("infra-up", "infra-down", "bake", "eval-smoke"):
        assert target not in makefile, (
            f"{MAKEFILE}: still declares `{target}` — it provisions or exercises "
            "infrastructure (ArcadeDB, the model cache) this tree does not have"
        )
    assert "arcadedb" not in makefile.lower(), (
        f"{MAKEFILE}: still references ArcadeDB — the fork has no graph server"
    )
    assert "model_cache" not in makefile, (
        f"{MAKEFILE}: still references the model cache — the fork loads no model weights"
    )

    assert "bash scripts/gate.sh" in makefile, (
        f"{MAKEFILE}: the `gate` target no longer runs scripts/gate.sh"
    )
    gate = GATE_SH.read_text(encoding="utf-8")
    for check in (
        "ruff check",
        "ruff format",
        "mypy processrecall",
        "lint-imports",
        "bandit -r processrecall",
        "pytest",
        "--cov-fail-under=",
        "pip-audit",
    ):
        assert check in gate, (
            f"{GATE_SH}: no longer runs `{check}` — the service-free gate drops "
            "infrastructure, not checks"
        )

    for target in (
        "sonar-up",
        "sonar-down",
        "sonar-token",
        "sonar",
        "sonar-gate-init",
        "sonar-gate",
    ):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.MULTILINE), (
            f"{MAKEFILE}: the `{target}` target is gone — the local quality gate "
            "reports to a container on this machine and is not infrastructure the cull covers"
        )


# ─── Library code never provisions its own environment ──────────────────────


# Sites in processrecall/ that fetch a corpus at runtime, on the failure path of a lookup.
# This is a LEDGER THAT MUST SHRINK, not a list of blessed exceptions: every entry is a
# package installed from PyPI reaching the network mid-ingest, which fails offline and
# degrades silently. The provisioning step already exists in scripts/bake_models.py.
#
# The ledger is empty. Adding an entry is the thing this test exists to stop.
_KNOWN_RUNTIME_DOWNLOADS: set[str] = set()


def _downloads_at_runtime(source: str) -> bool:
    """Whether this module CALLS ``nltk.download(...)``.

    Parsed, not grepped: a regex over the text also matches the call named inside a
    docstring or a comment, so documenting the rule would violate it. That false
    positive is how a guard earns a `# noqa` instead of a fix.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # not importable anyway; other checks own that
        return False
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "download"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "nltk"
        for node in ast.walk(tree)
    )


class TestNoRuntimeDownloads:
    """A missing corpus is an environment defect, not something library code routes around.

    The failure this pins: #137 shipped `nltk.download("wordnet")` into `skos.py` because the
    corpus was absent from the image. The tests went green, so the success criterion was met,
    and a package strangers pip-install now reaches the internet during ingest. It does not
    even work — `scripts/bake_models.py:99` documents that `nltk.download()` ignores
    `NLTK_DATA` without an explicit `download_dir`, and neither call passes one.
    """

    def _offenders(self) -> set[str]:
        found = set()
        for path in (REPO_ROOT / "processrecall").rglob("*.py"):
            if _downloads_at_runtime(path.read_text(encoding="utf-8")):
                found.add(path.relative_to(REPO_ROOT).as_posix())
        return found

    def test_no_new_site_downloads_a_corpus(self) -> None:
        new = self._offenders() - _KNOWN_RUNTIME_DOWNLOADS
        assert not new, (
            f"library code downloads a corpus at runtime: {sorted(new)}. A missing corpus is an "
            "ENVIRONMENT defect — provision it in scripts (see scripts/bake_models.py, which "
            "already fetches these) and fail loud here instead."
        )

    def test_the_ledger_shrinks_and_is_never_padded(self) -> None:
        fixed = _KNOWN_RUNTIME_DOWNLOADS - self._offenders()
        assert not fixed, (
            f"{sorted(fixed)} no longer download at runtime — remove them from "
            "_KNOWN_RUNTIME_DOWNLOADS so the ledger cannot be padded back out. See #247."
        )


class TestPreflight:
    def _load_module(self, tmp_path=None):
        spec = importlib.util.spec_from_file_location("preflight_under_test", PREFLIGHT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_lock_matching_removed(self) -> None:
        module = self._load_module()
        assert hasattr(module, "check_declared_dependencies_installed"), (
            f"{PREFLIGHT}: no `check_declared_dependencies_installed` — the "
            "declared-but-missing guard must survive"
        )
        assert not hasattr(module, "check_lock_matches"), (
            f"{PREFLIGHT}: `check_lock_matches` still exists — the lock-hash "
            "staleness check has nothing to compare against once the image "
            "installs with `uv sync` from the mounted uv.lock"
        )
        assert not hasattr(module, "BAKED_LOCK_HASH"), (
            f"{PREFLIGHT}: `BAKED_LOCK_HASH` still exists — no lock-hash file "
            "is baked once the lock-hash guard is removed"
        )

    def test_declared_dependency_check_still_works(self, tmp_path, monkeypatch) -> None:
        module = self._load_module()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\ndependencies = ["definitely-not-installed-pkg-xyz"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(module, "APP", tmp_path)
        result = module.check_declared_dependencies_installed()
        assert "definitely-not-installed-pkg-xyz" in result, (
            f"{PREFLIGHT}: check_declared_dependencies_installed() did not "
            f"name the missing package, got: {result!r}"
        )

        pyproject.write_text('[project]\ndependencies = ["pytest"]\n', encoding="utf-8")
        result = module.check_declared_dependencies_installed()
        assert result == "", (
            f"{PREFLIGHT}: check_declared_dependencies_installed() should "
            f"return '' when the declared dependency is installed, got: {result!r}"
        )

    def test_declared_extras_are_asserted(self, tmp_path, monkeypatch) -> None:
        module = self._load_module()
        pyproject = tmp_path / "pyproject.toml"
        monkeypatch.setattr(module, "APP", tmp_path)

        # RED: image claims the `srl` extra but its distribution isn't installed.
        pyproject.write_text(
            "[project]\ndependencies = []\n"
            "[project.optional-dependencies]\n"
            'srl = ["definitely-not-installed-pkg-xyz"]\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("GRAPHKNOWS_IMAGE_EXTRAS", "srl")
        result = module.check_declared_extras_installed()
        assert "srl" in result and "definitely-not-installed-pkg-xyz" in result, (
            f"{PREFLIGHT}: check_declared_extras_installed() did not name the "
            f"missing extra and package, got: {result!r}"
        )

        # An image that legitimately never claimed the extra is fine.
        monkeypatch.delenv("GRAPHKNOWS_IMAGE_EXTRAS", raising=False)
        result = module.check_declared_extras_installed()
        assert result == "", (
            f"{PREFLIGHT}: check_declared_extras_installed() should return "
            f"'' when the image does not claim the extra, got: {result!r}"
        )

        # An image that claims the extra and actually has it is fine.
        pyproject.write_text(
            '[project]\ndependencies = []\n[project.optional-dependencies]\nsrl = ["pytest"]\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("GRAPHKNOWS_IMAGE_EXTRAS", "srl")
        result = module.check_declared_extras_installed()
        assert result == "", (
            f"{PREFLIGHT}: check_declared_extras_installed() should return "
            f"'' when the claimed extra's distributions are installed, got: {result!r}"
        )

    def test_main_has_no_silent_degradation_guard(self) -> None:
        text = PREFLIGHT.read_text(encoding="utf-8")
        assert "except" not in text, (
            f"{PREFLIGHT}: contains `except` — a guard against silent "
            "degradation must not itself swallow errors silently"
        )


# ─── Analysis tool gone ──────────────────────────────────────────────────────


def _assert_gitignored(entry: str, why: str) -> None:
    # Asserting the .gitignore rule, not the entry's absence on disk: the rule
    # is what survives a clone, while the entry can legitimately exist
    # untracked in a developer tree. (`git ls-files` is not usable here: a
    # linked worktree's .git file names an absolute host path a container
    # mount cannot resolve.)
    entries = GITIGNORE.read_text(encoding="utf-8").splitlines()
    assert entry in entries, f"{GITIGNORE}: no `{entry}` entry — {why}"


class TestAnalysisToolRemoved:
    def test_no_sonar_project_properties(self) -> None:
        assert not (REPO_ROOT / "sonar-project.properties").exists(), (
            "sonar-project.properties: still exists — the analysis tool is removed"
        )

    def test_sonar_directory_is_gitignored(self) -> None:
        _assert_gitignored(".sonar", "the local analysis-tool cache could be committed by accident")

    @pytest.mark.parametrize("path", [PRE_COMMIT_CONFIG, CI_WORKFLOW])
    def test_no_sonar_references(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        assert "sonar" not in text.lower(), f"{path}: still references Sonar"
        assert "skip_sonar" not in text, f"{path}: still references skip_sonar"


# ─── Lint/test config folded into pyproject.toml ────────────────────────────


class TestLintTestConfigFolded:
    def test_pytest_ini_and_ruff_toml_removed(self) -> None:
        assert not (REPO_ROOT / "pytest.ini").exists(), "pytest.ini: still exists on disk"
        assert not (REPO_ROOT / "ruff.toml").exists(), "ruff.toml: still exists on disk"

    def test_nothing_still_references_ruff_toml(self) -> None:
        for path in [PRE_COMMIT_CONFIG, CI_WORKFLOW, MAKEFILE, GATE_SH]:
            text = path.read_text(encoding="utf-8")
            assert "ruff.toml" not in text, (
                f"{path}: still references ruff.toml — config must be folded "
                "into pyproject.toml and every caller pointed at it"
            )

    def test_pytest_ini_options_in_pyproject(self) -> None:
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        ini_options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
        assert ini_options, (
            f"{PYPROJECT}: no [tool.pytest.ini_options] — pytest.ini's config "
            "must be folded in here"
        )
        testpaths = ini_options.get("testpaths")
        assert testpaths == ["tests"] or testpaths == "tests", (
            f"{PYPROJECT}: tool.pytest.ini_options.testpaths == {testpaths!r}, expected 'tests'"
        )
        assert ini_options.get("asyncio_mode") == "auto", (
            f"{PYPROJECT}: tool.pytest.ini_options.asyncio_mode is not 'auto'"
        )
        addopts = ini_options.get("addopts", "")
        assert "--import-mode=importlib" in addopts, (
            f"{PYPROJECT}: tool.pytest.ini_options.addopts is missing --import-mode=importlib"
        )
        pythonpath = ini_options.get("pythonpath", [])
        assert "." in pythonpath, (
            f"{PYPROJECT}: tool.pytest.ini_options.pythonpath does not contain '.'"
        )
        markers = " ".join(ini_options.get("markers", []))
        assert "integration" in markers, (
            f"{PYPROJECT}: tool.pytest.ini_options.markers does not mention 'integration'"
        )
        assert "unit" in markers, (
            f"{PYPROJECT}: tool.pytest.ini_options.markers does not mention 'unit'"
        )

    def test_ruff_config_in_pyproject(self) -> None:
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        ruff = data.get("tool", {}).get("ruff", {})
        assert ruff, f"{PYPROJECT}: no [tool.ruff] — ruff.toml's config must be folded in here"
        assert ruff.get("line-length") == 100, (
            f"{PYPROJECT}: tool.ruff.line-length == {ruff.get('line-length')!r}, expected 100"
        )
        assert ruff.get("target-version") == "py311", (
            f"{PYPROJECT}: tool.ruff.target-version == "
            f"{ruff.get('target-version')!r}, expected 'py311'"
        )
        lint = ruff.get("lint", {})
        select = set(lint.get("select", []))
        required_select = {
            "E",
            "F",
            "I",
            "D",
            "C90",
            "N",
            "UP",
            "B",
            "BLE",
            "TRY",
            "SIM",
            "ERA",
            "RUF",
            "S110",
            "S112",
        }
        missing_select = required_select - select
        assert not missing_select, f"{PYPROJECT}: tool.ruff.lint.select is missing {missing_select}"
        ignore = set(lint.get("ignore", []))
        assert {"BLE001", "E501"} <= ignore, (
            f"{PYPROJECT}: tool.ruff.lint.ignore is missing { {'BLE001', 'E501'} - ignore }"
        )
        per_file_ignores = lint.get("per-file-ignores", {})
        assert "tests/*" in per_file_ignores, (
            f"{PYPROJECT}: tool.ruff.lint.per-file-ignores has no `tests/*` entry"
        )
        assert lint.get("pydocstyle", {}).get("convention") == "google", (
            f"{PYPROJECT}: tool.ruff.lint.pydocstyle.convention != 'google'"
        )
        assert lint.get("mccabe", {}).get("max-complexity") == 10, (
            f"{PYPROJECT}: tool.ruff.lint.mccabe.max-complexity != 10"
        )
        assert ruff.get("format", {}).get("quote-style") == "double", (
            f"{PYPROJECT}: tool.ruff.format.quote-style != 'double'"
        )


# ─── Root de-cluttered ───────────────────────────────────────────────────────


class TestRootDecluttered:
    def test_devcontainer_removed(self) -> None:
        assert not (REPO_ROOT / ".devcontainer").exists(), (
            ".devcontainer: still exists — no dev-container config for a Python-only repo"
        )

    def test_gitignore_has_no_node_rules(self) -> None:
        text = GITIGNORE.read_text(encoding="utf-8")
        for needle in [
            "node_modules",
            ".pnpm-store",
            ".turbo",
            "tsbuildinfo",
            "apps/**",
            "packages/**",
        ]:
            assert needle not in text, (
                f"{GITIGNORE}: still ignores {needle!r} — no Node in this repo"
            )

    def test_makefile_has_no_removed_targets(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        # NOT "python -m build": build-wheel legitimately uses it (see
        # pyproject.toml's build backend) — that target was never part of
        # this cleanup's scope.
        for needle in ["observability", "langfuse", "install-hooks", "SVC=web"]:
            assert needle not in text, f"{MAKEFILE}: still references {needle!r}"


# ─── Docs ─────────────────────────────────────────────────────────────────────


class TestDocs:
    def test_readme_has_no_deploy_section(self) -> None:
        text = README.read_text(encoding="utf-8")
        assert "## Deploy" not in text, (
            f"{README}: still has a `## Deploy` heading — there is no shipped "
            "deployment stack to document"
        )
        assert "docker-compose.prod.yaml" not in text, (
            f"{README}: still references docker-compose.prod.yaml"
        )
        assert "docker-smoke" not in text, f"{README}: still references docker-smoke"
        assert "bake all of them at build time" not in text, (
            f"{README}: still claims the Docker images bake models at build time"
        )


# ─── Per-project graph snapshot ──────────────────────────────────────────────


def test_project_snapshot_is_gitignored() -> None:
    # The per-project snapshot is written into the project it describes, so
    # every working tree grows one; it stays out of commits by default and a
    # deliberate `git add -f` remains possible.
    _assert_gitignored(
        ".processrecall/", "the per-project graph snapshot would be committed by accident"
    )
