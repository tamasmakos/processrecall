#!/bin/sh
# Prepare the plugin's runtime environment, exactly once (FR-068, FR-069, R14).
#
# The fast path is the point: $CLAUDE_PLUGIN_DATA/venv/.ready holds a checksum of
# uv.lock joined to the plugin root, so every session after the first exits here
# having invoked nothing and touched no network. Either half of that key going
# stale is the whole of the upgrade path.
#
# It is keyed on the lock because the marketplace tracks `main`, where every
# merge that changes uv.lock must resync. It carries the
# root because `uv sync` installs the project editable (uv.lock: source =
# { editable = "." }), so the venv's `processrecall` is a pointer back to
# $CLAUDE_PLUGIN_ROOT; a marketplace install puts each checkout in its own
# directory while the data directory stays put, and a venv still pointing at the
# previous, possibly deleted, root is broken. The same editability is why a
# code-only change at an unchanged lock and root needs no sync at all: it is
# live the moment the files change.
set -u

# Every failure leaves by here, as one line of JSON on stdout. The escaping is
# not decoration: the paths interpolated below are Windows paths under Git Bash,
# and a raw backslash makes the object unparseable, which costs the user the
# message entirely rather than merely its formatting.
report() {
    printf '{"systemMessage": "%s"}\n' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

root="${CLAUDE_PLUGIN_ROOT:-}"
data="${CLAUDE_PLUGIN_DATA:-}"
[ -n "$root" ] && [ -n "$data" ] || exit 0

# `cksum` and not sha256sum/shasum: it is POSIX, so it is present wherever this
# `sh` is, which the others are not across macOS, Linux and Git Bash.
key=$(cksum 2>/dev/null < "$root/uv.lock")
if [ -z "$key" ]; then
    report "processrecall cannot prepare its environment: cannot read $root/uv.lock"
    exit 0
fi
key="$key $root"

venv="$data/venv"
ready="$venv/.ready"
[ "$(cat "$ready" 2>/dev/null)" = "$key" ] && exit 0

# Reported, never raised: a SessionStart exiting non-zero surfaces against the
# developer's own session, which FR-069 forbids.
if ! command -v uv >/dev/null 2>&1; then
    # Claude Code runs this under Git Bash on Windows, where the shell is POSIX
    # but the installer is not; emit the one command the user can actually paste.
    case "$(uname -s 2>/dev/null)" in
        MINGW* | MSYS* | CYGWIN*)
            install_uv='powershell -c "irm https://astral.sh/uv/install.ps1 | iex"' ;;
        *)
            install_uv='curl -LsSf https://astral.sh/uv/install.sh | sh' ;;
    esac
    report "processrecall cannot prepare its environment: uv is not installed. Install it with: $install_uv"
    exit 0
fi

# --no-dev: ruff, mypy, bandit and pytest are 151 MB an end user never runs. The
# repo's own dev environment is a separate venv and is unaffected.
if UV_PROJECT_ENVIRONMENT="$venv" uv sync --frozen --no-dev --project "$root" >/dev/null 2>&1; then
    # hooks.json and .claude-plugin/mcp.json address the interpreter at the POSIX
    # $venv/bin/python path; on Windows uv lays the venv out as
    # $venv/Scripts/python.exe instead, so mirror it there. The venv
    # launcher finds pyvenv.cfg by walking up from its own directory,
    # so a copy one level deeper under bin/ still resolves correctly.
    if [ ! -e "$venv/bin/python" ] && [ -e "$venv/Scripts/python.exe" ]; then
        mkdir -p "$venv/bin" && cp "$venv/Scripts/python.exe" "$venv/bin/python.exe"
    fi
    mkdir -p "$venv" && printf '%s' "$key" > "$ready"
else
    report "processrecall could not prepare its environment. Run it by hand to see why: UV_PROJECT_ENVIRONMENT=$venv uv sync --frozen --no-dev --project $root"
fi
exit 0
