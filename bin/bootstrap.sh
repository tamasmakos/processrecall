#!/bin/sh
# Prepare the plugin's runtime environment, exactly once (FR-068, FR-069, R14).
#
# The fast path is the point: $CLAUDE_PLUGIN_DATA/venv/.ready holds the plugin
# version it was built for, so every session after the first exits here having
# invoked nothing and touched no network. A version bump makes the marker stale,
# which is the whole of the upgrade path.
set -u

root="${CLAUDE_PLUGIN_ROOT:-}"
data="${CLAUDE_PLUGIN_DATA:-}"
[ -n "$root" ] && [ -n "$data" ] || exit 0

version=$(
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$root/.claude-plugin/plugin.json" 2>/dev/null | head -n 1
)
if [ -z "$version" ]; then
    printf '{"systemMessage": "processrecall cannot prepare its environment: no version in %s/.claude-plugin/plugin.json"}\n' "$root"
    exit 0
fi

venv="$data/venv"
ready="$venv/.ready"
[ "$(cat "$ready" 2>/dev/null)" = "$version" ] && exit 0

# Reported, never raised: a SessionStart exiting non-zero surfaces against the
# developer's own session, which FR-069 forbids.
if ! command -v uv >/dev/null 2>&1; then
    printf '{"systemMessage": "processrecall cannot prepare its environment: uv is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"}\n'
    exit 0
fi

if UV_PROJECT_ENVIRONMENT="$venv" uv sync --frozen --project "$root" >/dev/null 2>&1; then
    mkdir -p "$venv" && printf '%s' "$version" > "$ready"
else
    printf '{"systemMessage": "processrecall could not prepare its environment. Run it by hand to see why: UV_PROJECT_ENVIRONMENT=%s uv sync --frozen --project %s"}\n' \
        "$venv" "$root"
fi
exit 0
