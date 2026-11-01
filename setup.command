#!/bin/bash
# Finder and Terminal launcher; works independently of the caller's directory.
set -eu
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
find_python() {
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            return 0
        fi
    done
    return 1
}
if ! find_python; then
    if ! command -v brew >/dev/null 2>&1; then
        echo 'Python 3.9+ is missing. Install Python from https://www.python.org/downloads/macos/ or Homebrew from https://brew.sh, then rerun setup.command.' >&2
        exit 2
    fi
    echo 'Python 3.9+ is required. Install it with: brew install python'
    read -r -p 'Allow this installation? [y/N] ' answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *) echo 'Installation declined.'; exit 2 ;;
    esac
    # No GitHub credentials are inherited by Homebrew or its download processes.
    if ! /usr/bin/env -i HOME="$HOME" PATH="$PATH" USER="${USER:-}" TMPDIR="${TMPDIR:-/tmp}" /bin/bash -c 'exec brew install python'; then
        echo 'Python installation failed. Resolve the Homebrew error and rerun setup.command.' >&2
        exit 2
    fi
    if ! find_python; then
        echo 'Python installation finished but Python 3.9+ was not found. Check your Homebrew installation.' >&2
        exit 2
    fi
fi
exec "$PYTHON_BIN" guided_setup.py "$@"
