#!/usr/bin/env bash
# No shell-command arguments: only the bridge's validated bind/port flags.
set -euo pipefail
DECK_WEB_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$DECK_WEB_DIR/bridge.py" "$@"
