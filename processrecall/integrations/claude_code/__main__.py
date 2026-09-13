"""``python -m processrecall.integrations.claude_code <verb>`` — the hook entry point.

The shipped ``settings.json`` names the package, not the module, so the command a
user copies keeps working if the verb implementation ever moves.

The stdin/stdout framing and six-verb dispatch this delegates to are being
rewritten onto :func:`~processrecall.integrations.claude_code.hooks.adapt_post_tool_use`
(T024); there is nothing to run here until that lands.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.exit("processrecall.integrations.claude_code: hook dispatch pending T024")
