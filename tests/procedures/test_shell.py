"""The command grammar seam: one shell command line -> its simple commands.

A Bash action is one step but rarely one activity (FR-017): ``pytest | tail`` is an
evaluation *and* an inspection, and a node named after the whole line would be a
node no second step ever matches. So the grammar is tested at what it yields —
the simple commands, the program each names, and the files each touches — never
at the lexer inside it.
"""

from __future__ import annotations

from processrecall.config import ActivityClass
from processrecall.procedures.shell import (
    artifacts_in,
    classify_program,
    subcommands,
    unwrap,
)


def test_pipeline_yields_one_command_per_stage() -> None:
    assert subcommands("pytest -q | tail -n 5") == [["pytest", "-q"], ["tail", "-n", "5"]]


def test_chained_commands_split_on_every_operator_and_on_newlines() -> None:
    command = "git add -A && git commit -m wip; git status\ngit log"
    assert subcommands(command) == [
        ["git", "add", "-A"],
        ["git", "commit", "-m", "wip"],
        ["git", "status"],
        ["git", "log"],
    ]


def test_an_operator_inside_a_quoted_argument_is_content_not_a_separator() -> None:
    assert subcommands("rg 'passed|failed' -n") == [["rg", "passed|failed", "-n"]]


def test_an_unbalanced_quote_degrades_to_a_naive_split_rather_than_raising() -> None:
    """Capture runs beside the agent: a command we cannot lex is still a step."""
    assert subcommands("echo it's fine | wc -l") == [["echo", "it's", "fine"], ["wc", "-l"]]


def test_a_subshell_body_decomposes_into_the_commands_it_runs() -> None:
    """``docker exec … sh -c '…'`` is the shape half this project's commands take."""
    assert subcommands("""sh -c 'ruff check . && mypy processrecall'""") == [
        ["ruff", "check", "."],
        ["mypy", "processrecall"],
    ]


def test_a_subshell_group_decomposes_into_the_commands_it_runs() -> None:
    """``( … )`` groups commands without naming a program of its own."""
    assert subcommands("(cd /app && pytest -q)") == [
        ["cd", "/app"],
        ["pytest", "-q"],
    ]


def test_env_assignments_and_wrappers_fall_away_so_the_program_leads() -> None:
    assert subcommands("PYTHONPATH=. timeout 30 pytest -q") == [["pytest", "-q"]]


def test_a_value_that_looks_like_an_env_assignment_is_not_stripped_mid_command() -> None:
    """Only a leading ``VAR=value`` run is an assignment; one later is an argument."""
    assert subcommands("docker run -e FOO=1 img pytest") == [["pytest"]]
    assert subcommands("curl -d name=value url") == [["curl", "-d", "name=value", "url"]]


def test_a_heredoc_body_is_content_not_a_run_of_commands() -> None:
    command = "cat > notes.md <<'EOF'\nrm -rf everything | true\nEOF"
    assert subcommands(command) == [["cat", ">", "notes.md"]]


def test_unwrap_drops_docker_exec_flags_and_the_container_it_runs_in() -> None:
    tokens = ["docker", "exec", "-w", "/app", "-i", "processrecall-workspace", "pytest", "-q"]
    assert unwrap(tokens) == ["pytest", "-q"]


def test_unwrap_peels_nested_runners_down_to_the_program_that_did_the_work() -> None:
    assert unwrap(["docker", "exec", "box", "uv", "run", "mypy", "."]) == ["mypy", "."]


def test_unwrap_leaves_docker_alone_when_docker_is_the_program() -> None:
    assert unwrap(["docker", "compose", "up", "-d"]) == ["docker", "compose", "up", "-d"]


def test_a_tabled_program_names_its_activity_class() -> None:
    assert classify_program(["pytest", "-q"]) == (ActivityClass.ARTIFACT_EVALUATION, "pytest")


def test_the_verb_decides_for_a_program_that_does_many_things() -> None:
    """``git`` is not an activity; ``git commit`` and ``git log`` are different ones."""
    assert classify_program(["git", "commit", "-m", "wip"]) == (
        ActivityClass.CHECKIN,
        "git commit",
    )
    assert classify_program(["git", "log", "--oneline"]) == (
        ActivityClass.INSPECTION,
        "git log",
    )


def test_an_untabled_verb_falls_back_to_the_programs_own_default() -> None:
    """Every ``gh`` verb reaches GitHub, listed or not."""
    assert classify_program(["gh", "pr", "create"]) == (
        ActivityClass.NETWORK_RETRIEVAL,
        "gh pr",
    )


def test_an_untabled_program_is_unknown_and_keeps_its_name() -> None:
    """``Unknown`` is a member of the vocabulary, not a failure (FR-022)."""
    assert classify_program(["terraform", "apply"]) == (ActivityClass.UNKNOWN, "terraform")


def test_a_builtin_is_no_activity_at_all_rather_than_an_unknown_one() -> None:
    """``cd`` and ``echo`` would otherwise bury the real residue under noise."""
    assert classify_program(["cd", "/app"]) == (None, "cd")
    assert classify_program(["done"]) == (None, "done")


def test_python_dash_m_is_named_and_classified_by_the_module_it_runs() -> None:
    """``python -m pytest`` is an evaluation; ``python build.py`` is not."""
    assert classify_program(["python", "-m", "pytest", "-q"]) == (
        ActivityClass.ARTIFACT_EVALUATION,
        "python -m pytest",
    )
    assert classify_program(["python", "build.py"]) == (ActivityClass.SCRIPT_EXECUTION, "python")


def test_sed_in_place_is_a_change_not_an_inspection() -> None:
    assert classify_program(["sed", "-n", "1,20p", "shell.py"])[0] == ActivityClass.INSPECTION
    assert classify_program(["sed", "-i", "s/a/b/", "shell.py"])[0] == (
        ActivityClass.CHANGE_IMPLEMENTATION
    )


def test_a_fragment_that_is_not_a_program_is_no_activity_and_cannot_flood_a_name() -> None:
    """The naive split of an unlexable line leaves debris; it must not become a node."""
    fragment = "(passed|failed|errors during collection)"
    activity, program = classify_program([fragment])
    assert activity is None
    assert program == fragment[:20]


def test_a_path_argument_is_used_and_a_redirect_target_is_created() -> None:
    assert artifacts_in(["cp", "docs/design.md", ">", "build/out.md"]) == [
        ("uses", "docs/design.md"),
        ("creates", "build/out.md"),
    ]


def test_flags_urls_the_null_sink_and_bare_numbers_are_not_artifacts() -> None:
    """An artifact edge is how the graph conditions on a file; junk in it is noise."""
    [tokens] = subcommands("curl -sS https://example.com/a.json > /dev/null 2>&1")
    assert artifacts_in(tokens) == []
    assert artifacts_in(["head", "-n", "20", "3.11", "shell.py"]) == [("uses", "shell.py")]


def test_one_containerised_command_line_decomposes_into_the_activities_it_performed() -> None:
    """The whole grammar on the shape this project runs: runner, subshell, pipeline."""
    command = "docker exec -i box sh -c 'cd /app && pytest -q tests/ > out.txt'"
    assert [
        (*classify_program(tokens), artifacts_in(tokens)) for tokens in subcommands(command)
    ] == [
        (None, "cd", [("uses", "/app")]),
        (ActivityClass.ARTIFACT_EVALUATION, "pytest", [("uses", "tests/"), ("creates", "out.txt")]),
    ]
