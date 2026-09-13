"""One invocation -> the abstracted template the episodic row stores.

A template is what makes two steps the same step: ``git diff --stat <File>``
matches a second diff of a second file, where the literal command line never
would. The abstraction is also the containment (FR-051) — a path, a number or a
free-text argument is replaced rather than shortened, so a template cannot carry
a payload out of the private store and into a shared snapshot.

Lifted from prototype v5 (R18). On the hot path, so the standard library only.
"""

from __future__ import annotations

from pathlib import PurePath

from processrecall.procedures.shell import REDIRECT_OPERATORS, is_path
from processrecall.procedures.step import SubActivity

#: Past this many characters a bare token stops being a flag or a verb and starts
#: being content — a search pattern, a commit message, a here-string.
_LONGEST_LITERAL_CHARS = 40

#: What a whole redirect is spelled as, however many tokens it took to write.
_REDIRECT = "<redirect>"

#: How many template tokens a step keeps, and how many tokens of the invocation
#: are scanned to fill them — prototype v5 (R18, E4) reads only the head of the
#: line before this truncation, so a redirect past token 16 is never folded.
_LONGEST_TEMPLATE_TOKENS = 12
_SCANNED_TOKENS = 16


def template_of(activity: SubActivity) -> str:
    """The template of one sub-activity's invocation."""
    tokens = activity.tokens[:_SCANNED_TOKENS]
    out: list[str] = []
    index = 0
    while index < len(tokens) and len(out) < _LONGEST_TEMPLATE_TOKENS:
        if span := _redirect_span(tokens, index):
            if not out or out[-1] != _REDIRECT:
                out.append(_REDIRECT)
            index += span
            continue
        out.append(_abstract(tokens[index]))
        index += 1
    return " ".join(out)


def _redirect_span(tokens: tuple[str, ...], index: int) -> int:
    """How many tokens the redirect at *index* occupies, or ``0`` for no redirect.

    A redirect is written as an optional file descriptor, an operator and a
    target — ``2 > /dev/null``, ``> out.txt``, ``>& 1`` — and only the operator
    is always there.
    """
    token = tokens[index]
    following = tokens[index + 1] if index + 1 < len(tokens) else ""
    if token in REDIRECT_OPERATORS:
        span = 1
    elif token.isdigit() and following in REDIRECT_OPERATORS:
        span = 2
    else:
        return 0
    return span + (1 if index + span < len(tokens) else 0)


def _abstract(token: str) -> str:
    """One token, reduced to what it is rather than what it names.

    ``key=value`` is an assignment — a flag's inline argument or an environment
    variable — and the key names the shape while the value is content, so the
    value is abstracted on its own rather than kept verbatim (FR-051).
    """
    prefix, separator, value = token.partition("=")
    if separator:
        return f"{prefix}={_abstract_value(value)}"
    if is_path(token):
        return "<File>" if PurePath(token.replace("\\", "/")).suffix else "<Dir>"
    if token.isdigit():
        return "<N>"
    if " " in token or len(token) > _LONGEST_LITERAL_CHARS:
        return "<str>"
    return token


def _abstract_value(value: str) -> str:
    """An assignment's value half: always content, never passed through raw."""
    if is_path(value):
        return "<File>" if PurePath(value.replace("\\", "/")).suffix else "<Dir>"
    if value.isdigit():
        return "<N>"
    return "<str>" if value else ""
