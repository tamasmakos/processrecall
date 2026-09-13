"""``python -m processrecall.integrations.claude_code <verb>`` — the hook entry point.

The shipped ``settings.json`` names the package, not the module, so the command a
user copies keeps working if the verb implementation ever moves.

Dispatch is the whole of this module, and its one rule is the exit code. It is 0
on an unknown verb, on stdin that carries no event, and on any failure of the
verb itself: a non-zero exit surfaces against the developer's own action, which
FR-014 forbids. Nothing raised below is allowed past this frame — it is logged
instead, where a maintainer looking for it will find it.

The logging here (and in :func:`~processrecall.integrations.claude_code.hooks.read_payload`)
is today's fallback, and only reaches a maintainer who has configured the stdlib
``processrecall`` logger. R16's counter row and ``~/.processrecall/log/hooks.jsonl``
line land with T029.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from processrecall.integrations.claude_code.hooks import VERBS, emit, read_payload

_logger = logging.getLogger("processrecall")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the verb named in *argv* against the hook event on stdin; always 0."""
    args = list(sys.argv[1:] if argv is None else argv)
    verb = VERBS.get(args[0]) if args else None
    if verb is None:
        _logger.warning("hook invoked as %r, which names no verb of this plugin", args)
        return 0
    try:
        payload = read_payload(sys.stdin)
        if payload is not None and (response := verb(payload)) is not None:
            emit(response, sys.stdout)
    except Exception:
        _logger.exception("the %s hook failed; the agent's action is unaffected", args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
