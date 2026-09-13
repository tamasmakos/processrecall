"""The command grammar: one shell command line -> the simple commands it runs.

A Bash action arrives as one string and is rarely one activity (FR-017).
``uv run pytest -q | tail -5`` is an evaluation piped into an inspection; a node
named after the whole line would be a node no second step ever matches. So this
module cuts the line at its operators, strips the wrappers that hide the program
that matters, names the activity class of each remaining simple command, and
lists the files it touches.

Reconstructed from prototype v3 (R18): v3 was never tracked in this repository, so
this grammar is a rewrite against its description rather than a lift of its source.
On the hot path, so the standard library only.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from processrecall.symbolic.packs import ActivityClass

#: Interpreters whose ``-c`` body is itself a command line, not an argument.
_SHELLS = frozenset({"sh", "bash", "zsh", "pwsh", "powershell"})

#: Programs that run another program: the activity belongs to what they wrap.
_WRAPPERS = frozenset({"timeout", "time", "env", "sudo", "nice", "exec", "command", "nohup"})

#: ``VAR=value`` prefixing a command line — an assignment, not the program.
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: ``docker exec|run`` flags that take a separate value, so both tokens go.
_DOCKER_VALUE_FLAGS = frozenset({"-w", "--workdir", "-e", "--env", "-u", "--user", "--name"})

#: A token that names a file: it has a separator, or a short extension.
_PATHLIKE = re.compile(r"^(?:[A-Za-z]:)?[\w./\\~-]*(?:/|\\)[\w./\\~-]*$|^[\w-]+\.[A-Za-z0-9]{1,6}$")

#: Path-shaped tokens that are plumbing rather than artifacts of the project.
_NON_FILES = frozenset({"/dev/null"})

#: Below this, a path-shaped token is an option cluster rather than a file.
_SHORTEST_PATH_TOKEN = 3

#: What a program name may look like; anything else is shell syntax or debris.
_PROGRAM_TOKEN = re.compile(r"^\$?[\w./\\:-]+$")

#: How much of a non-program token is worth reporting back as its name.
_MAX_PROGRAM_LEN = 20

#: Builtins, control words and output verbs: no engineering activity happened.
_IGNORED = frozenset({
    "echo", "printf", "true", "false", "cd", "export", "set", "sleep", "tee", "exit", "read",
    "test", "[", "write-host", "write-output", "select-object", "measure-object",
    "foreach-object", "where-object", "sort-object", "for", "do", "done", "if", "then", "else",
    "elif", "fi", "while", "until", "case", "esac", "{", "}", "function", "return", "local",
})  # fmt: skip

_A = ActivityClass

#: Programs whose activity depends on their first non-flag argument. ``"*"`` is
#: the fallback for a verb the table does not list.
_GIT = {
    "commit": _A.CHECKIN, "add": _A.CHECKIN, "tag": _A.CHECKIN,
    "checkout": _A.CHECKOUT, "switch": _A.CHECKOUT, "branch": _A.CHECKOUT, "worktree": _A.CHECKOUT,
    "status": _A.INSPECTION, "log": _A.INSPECTION, "diff": _A.INSPECTION, "show": _A.INSPECTION,
    "remote": _A.INSPECTION, "rev-parse": _A.INSPECTION, "ls-files": _A.INSPECTION,
    "blame": _A.INSPECTION, "cat-file": _A.INSPECTION, "describe": _A.INSPECTION,
    "stash": _A.CHANGE_IMPLEMENTATION, "merge": _A.CHANGE_IMPLEMENTATION,
    "rebase": _A.CHANGE_IMPLEMENTATION, "reset": _A.CHANGE_IMPLEMENTATION,
    "restore": _A.CHANGE_IMPLEMENTATION, "rm": _A.CHANGE_IMPLEMENTATION,
    "apply": _A.CHANGE_IMPLEMENTATION, "cherry-pick": _A.CHANGE_IMPLEMENTATION,
    "fetch": _A.NETWORK_RETRIEVAL, "pull": _A.NETWORK_RETRIEVAL, "push": _A.NETWORK_RETRIEVAL,
    "clone": _A.NETWORK_RETRIEVAL, "ls-remote": _A.NETWORK_RETRIEVAL,
    "config": _A.ENVIRONMENT_CONFIGURATION, "init": _A.ENVIRONMENT_CONFIGURATION,
}  # fmt: skip
_GH = {
    "run": _A.INSPECTION, "auth": _A.INSPECTION, "search": _A.SEARCH,
    "label": _A.ENVIRONMENT_CONFIGURATION,
    "*": _A.NETWORK_RETRIEVAL,  # every other gh verb reaches GitHub
}  # fmt: skip
_UV = {
    "run": _A.SCRIPT_EXECUTION, "pip": _A.ENVIRONMENT_CONFIGURATION,
    "sync": _A.ENVIRONMENT_CONFIGURATION, "add": _A.ENVIRONMENT_CONFIGURATION,
    "lock": _A.ENVIRONMENT_CONFIGURATION,
}  # fmt: skip
_DOCKER = {
    "exec": _A.SCRIPT_EXECUTION, "run": _A.SCRIPT_EXECUTION,
    "compose": _A.ENVIRONMENT_CONFIGURATION, "build": _A.ENVIRONMENT_CONFIGURATION,
    "cp": _A.ENVIRONMENT_CONFIGURATION,
    "ps": _A.INSPECTION, "logs": _A.INSPECTION, "inspect": _A.INSPECTION,
    "history": _A.INSPECTION, "stats": _A.INSPECTION, "images": _A.INSPECTION,
    "version": _A.INSPECTION, "info": _A.INSPECTION,
}  # fmt: skip

#: ``python -m <module>``: the module is the program, so it decides the class.
_MODULES = {
    "pytest": _A.ARTIFACT_EVALUATION, "ruff": _A.ARTIFACT_EVALUATION, "mypy": _A.ARTIFACT_EVALUATION,
    "bandit": _A.ARTIFACT_EVALUATION, "pyright": _A.ARTIFACT_EVALUATION,
    "pip": _A.ENVIRONMENT_CONFIGURATION, "build": _A.ENVIRONMENT_CONFIGURATION,
}  # fmt: skip

#: Activity class per program; a nested table means the verb decides (CMPO for
#: configuration management, QAPO for evaluation, ACE for the rest).
_PROGRAMS: dict[str, ActivityClass | dict[str, ActivityClass]] = {
    "git": _GIT, "gh": _GH, "uv": _UV, "docker": _DOCKER,
    "pytest": _A.ARTIFACT_EVALUATION, "ruff": _A.ARTIFACT_EVALUATION, "mypy": _A.ARTIFACT_EVALUATION,
    "bandit": _A.ARTIFACT_EVALUATION, "pre-commit": _A.ARTIFACT_EVALUATION, "pyright": _A.ARTIFACT_EVALUATION,
    "python": _A.SCRIPT_EXECUTION, "python3": _A.SCRIPT_EXECUTION, "py": _A.SCRIPT_EXECUTION,
    "node": _A.SCRIPT_EXECUTION, "make": _A.SCRIPT_EXECUTION,
    "pip": _A.ENVIRONMENT_CONFIGURATION, "mkdir": _A.ENVIRONMENT_CONFIGURATION,
    "cp": _A.ENVIRONMENT_CONFIGURATION, "mv": _A.ENVIRONMENT_CONFIGURATION,
    "rm": _A.ENVIRONMENT_CONFIGURATION, "touch": _A.ENVIRONMENT_CONFIGURATION,
    "chmod": _A.ENVIRONMENT_CONFIGURATION, "ln": _A.ENVIRONMENT_CONFIGURATION,
    "cat": _A.INSPECTION, "ls": _A.INSPECTION, "head": _A.INSPECTION, "tail": _A.INSPECTION,
    "wc": _A.INSPECTION, "diff": _A.INSPECTION, "jq": _A.INSPECTION, "du": _A.INSPECTION,
    "date": _A.INSPECTION, "stat": _A.INSPECTION, "file": _A.INSPECTION, "which": _A.INSPECTION,
    "type": _A.INSPECTION, "pwd": _A.INSPECTION, "sed": _A.INSPECTION, "awk": _A.INSPECTION,
    "sort": _A.INSPECTION, "uniq": _A.INSPECTION, "cut": _A.INSPECTION, "tr": _A.INSPECTION,
    "xargs": _A.INSPECTION,
    "grep": _A.SEARCH, "rg": _A.SEARCH, "find": _A.SEARCH, "ag": _A.SEARCH,
    "curl": _A.NETWORK_RETRIEVAL, "wget": _A.NETWORK_RETRIEVAL,
    "get-content": _A.INSPECTION, "get-childitem": _A.INSPECTION, "test-path": _A.INSPECTION,
    "get-command": _A.INSPECTION, "select-string": _A.SEARCH,
    "remove-item": _A.ENVIRONMENT_CONFIGURATION, "new-item": _A.ENVIRONMENT_CONFIGURATION,
    "copy-item": _A.ENVIRONMENT_CONFIGURATION, "move-item": _A.ENVIRONMENT_CONFIGURATION,
    "set-content": _A.CHANGE_IMPLEMENTATION, "out-file": _A.CHANGE_IMPLEMENTATION,
    "add-content": _A.CHANGE_IMPLEMENTATION,
}  # fmt: skip

#: Shell operators that end one simple command and start the next.
_OPERATORS = frozenset({"&&", "||", ";", "|", "&"})

#: The same cut, without quote awareness — only for a line :mod:`shlex` refuses.
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def _newlines_to_semicolons(command: str) -> str:
    """A newline outside quotes separates commands; inside quotes it is content."""
    out: list[str] = []
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == "\n":
            out.append(" ; ")
            continue
        out.append(char)
    return "".join(out)


def subcommands(command: str) -> list[list[str]]:
    """The simple commands of a shell command line, as token lists.

    ``punctuation_chars`` makes :mod:`shlex` emit ``&&``/``|``/``;`` as tokens of
    their own, so a ``|`` inside a quoted grep pattern no longer splits the line.
    A heredoc body is the written document, not a run of commands, so the line
    ends where its ``<<`` begins.
    """
    command = command.split("<<", 1)[0]
    lexer = shlex.shlex(_newlines_to_semicolons(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    groups: list[list[str]] = []
    tokens: list[str] = []
    try:
        for token in lexer:
            if token in ("(", ")"):  # a subshell group, not a program of its own
                continue
            if token in _OPERATORS:
                if tokens:
                    groups.append(tokens)
                tokens = []
                continue
            tokens.append(token)
    except ValueError:  # unbalanced quotes: a naive split still names the programs
        groups = [part.split() for part in _SPLIT.split(command) if part.strip()]
    else:
        if tokens:
            groups.append(tokens)
    return [simple for group in groups for simple in _expand(group)]


def _program_name(token: str) -> str:
    """The bare, lower-cased program name a token invokes, ``.exe`` and all."""
    return Path(token.lstrip("$")).name.lower().removesuffix(".exe")


def classify_program(tokens: list[str]) -> tuple[ActivityClass | None, str]:
    """``(activity class, program)`` for one simple command.

    ``None`` and ``UNKNOWN`` are different verdicts: ``None`` says the token is
    not an activity at all — a builtin, a control word — and the caller drops it,
    while ``UNKNOWN`` is a real invocation the tables do not know, kept and
    counted so the residue stays visible (FR-022).
    """
    if not _PROGRAM_TOKEN.match(tokens[0]):
        return None, tokens[0][:_MAX_PROGRAM_LEN]
    program = _program_name(tokens[0])
    if program in _IGNORED:
        return None, program
    if program in ("python", "python3", "py") and tokens[1:2] == ["-m"] and len(tokens) > 2:
        module = tokens[2].split(".")[0]
        return _MODULES.get(module, ActivityClass.SCRIPT_EXECUTION), f"python -m {module}"
    entry = _PROGRAMS.get(program, ActivityClass.UNKNOWN)
    if isinstance(entry, dict):
        verb = next((token for token in tokens[1:] if not token.startswith("-")), "")
        table, program = entry, f"{program} {verb}"
        if verb in table:
            entry = table[verb]
        elif verb in ("", "help", "--version"):
            entry = ActivityClass.INSPECTION
        else:
            entry = table.get("*", ActivityClass.UNKNOWN)
    if program == "sed" and any(token.startswith("-i") for token in tokens[1:]):
        entry = ActivityClass.CHANGE_IMPLEMENTATION
    return entry, program


def _is_path(token: str) -> bool:
    """Whether a token names a file this command touched.

    A flag, a null sink, a version number and a bare integer are all path-shaped
    enough to fool the pattern, and each would add an artifact the step never
    read or wrote.
    """
    if token.startswith("-") or token in _NON_FILES or token.replace(".", "").isdigit():
        return False
    return bool(_PATHLIKE.match(token)) and len(token) >= _SHORTEST_PATH_TOKEN


def artifacts_in(tokens: list[str]) -> list[tuple[str, str]]:
    """The ``(verb, path)`` pairs one simple command touches.

    A redirect target is created by the command; every other path-shaped token is
    used. The verb is what the episodic edge to the artifact is labelled with, so
    a wrong one would claim an inspection wrote a file.
    """
    out: list[tuple[str, str]] = []
    verb = "uses"
    for token in tokens[1:]:
        if token in (">", ">>"):
            verb = "creates"
            continue
        if _is_path(token):
            out.append((verb, token))
        verb = "uses"
    return out


def unwrap(tokens: list[str]) -> list[str]:
    """Strip ``docker exec|run <container>`` and ``uv run`` down to the inner program.

    The runner is not the activity: ``docker exec box pytest`` is an evaluation,
    and a node keyed on ``docker exec`` would merge it with every other command
    this project happens to run inside the workspace.
    """
    program = _program_name(tokens[0])
    if program == "docker" and tokens[1:2] in (["exec"], ["run"]) and len(tokens) > 2:
        rest = tokens[2:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in _DOCKER_VALUE_FLAGS else rest[1:]
        rest = rest[1:]  # the container (exec) or the image (run)
        return unwrap(rest) if rest else tokens
    if program == "uv" and tokens[1:2] == ["run"] and len(tokens) > 2:
        return unwrap(tokens[2:])
    return tokens


def _expand(tokens: list[str]) -> list[list[str]]:
    """One token group -> its simple commands, wrappers and shell bodies resolved."""
    while tokens and _ENV_ASSIGN.match(tokens[0]):
        tokens = tokens[1:]
    while tokens and (tokens[0].lower() in _WRAPPERS or tokens[0].startswith("-")):
        tokens = tokens[1:]
        if tokens and tokens[0].isdigit():  # timeout 30 <program>
            tokens = tokens[1:]
    if not tokens:
        return []
    tokens = unwrap(tokens)
    program = _program_name(tokens[0])
    if program not in _SHELLS:
        return [tokens]
    body = next((token for token in tokens[1:] if not token.startswith("-")), "")
    return subcommands(body) if body else []
