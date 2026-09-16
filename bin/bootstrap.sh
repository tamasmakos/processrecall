#!/bin/sh
# Prepare the plugin's runtime environment, exactly once (FR-068, FR-069, R14).
#
# The fast path is the point: $CLAUDE_PLUGIN_DATA/venv/.ready holds the version
# pinned in .claude-plugin/plugin.json, so every session after the first exits
# here having invoked nothing and touched no network. That pin going stale is the
# whole of the upgrade path.
#
# It is keyed on the pin because the venv holds a released `processrecall`
# installed from the index, not the checkout it was unpacked from: a new pin is a
# new release to install, and nothing else about the root can change what the
# venv holds. A marketplace install putting each checkout in its own directory is
# therefore no longer a hazard the key has to catch.
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

manifest="$root/.claude-plugin/plugin.json"
# POSIX `sed` and not a JSON parser: this runs before the environment that would
# hold one exists, so the only tools available are the ones `sh` came with.
key=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" 2>/dev/null)
if [ -z "$key" ]; then
    report "processrecall cannot prepare its environment: cannot read a version from $manifest"
    exit 0
fi

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

# The pinned release off the index, into a venv of its own: no --project, no lock
# and no editable install, so what the venv holds is exactly the distribution the
# manifest names and nothing about $root leaks into it.
if uv venv "$venv" >/dev/null 2>&1 &&
    VIRTUAL_ENV="$venv" uv pip install "processrecall==$key" >/dev/null 2>&1; then
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
    report "processrecall could not prepare its environment. Run it by hand to see why: uv venv $venv && VIRTUAL_ENV=$venv uv pip install processrecall==$key"
fi
exit 0
