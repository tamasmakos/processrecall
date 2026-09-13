"""The error hierarchy the fork leaves behind (T009, plan §Project Structure).

Three errors survive, one per thing that can go wrong on its own terms: the
runtime environment was never prepared (FR-069), a curated vocabulary pack is
malformed (FR-024), and an annotation failed validation before storage
(FR-038a). Each is tested at the seam a caller sees: the message it renders and
the attributes it carries.
"""

from __future__ import annotations

import pytest

from processrecall import exceptions
from processrecall.exceptions import AnnotationRejected, BootstrapError, PackError

pytestmark = pytest.mark.unit


def test_bootstrap_error_names_the_missing_tool_and_the_remedy() -> None:
    """FR-069: one message, actionable — what is missing and how to get it."""
    error = BootstrapError("uv", "install it from https://docs.astral.sh/uv/")

    assert error.tool == "uv"
    assert "uv" in str(error)
    assert "install it from https://docs.astral.sh/uv/" in str(error)


def test_pack_error_names_the_pack_and_what_is_wrong_with_it() -> None:
    """FR-024 / contracts/packs.md: a malformed pack raises a *named* error."""
    error = PackError("seon_activities", "concept 'Inspection' has no definition")

    assert error.pack == "seon_activities"
    assert "seon_activities" in str(error)
    assert "concept 'Inspection' has no definition" in str(error)


def test_annotation_rejected_carries_the_reason_code_the_tool_reports() -> None:
    """FR-038a: the reason is a code the ``remember`` tool reports verbatim.

    ``contracts/mcp-tools.md`` fixes the three codes (``no_such_edge``,
    ``too_long``, ``credential``) and requires each rejection to carry detail the
    agent can act on, so the code and the detail stay separately readable.
    """
    error = AnnotationRejected("too_long", "the note is 812 characters; the limit is 500")

    assert error.reason == "too_long"
    assert "too_long" in str(error)
    assert "the note is 812 characters; the limit is 500" in str(error)
    assert error.reason in AnnotationRejected.REASONS


def test_annotation_rejected_reasons_are_the_three_the_tool_reports() -> None:
    """contracts/mcp-tools.md fixes exactly these three codes."""
    assert {"no_such_edge", "too_long", "credential"} == AnnotationRejected.REASONS


def test_the_module_offers_exactly_the_three_surviving_errors() -> None:
    """T009: the pre-fork hierarchy went with the layers that raised it.

    A class nobody raises is dead weight, and a base class kept "just in case"
    invites ``except GraphKnowsError`` handlers that catch nothing. The module is
    the whole list.
    """
    defined = {
        name
        for name, value in vars(exceptions).items()
        if isinstance(value, type) and name[0].isupper()
    }

    assert defined == {"AnnotationRejected", "BootstrapError", "PackError"}
