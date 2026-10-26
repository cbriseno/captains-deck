#!/bin/sh
# Compatibility entrypoint for existing private cron/service configurations.
set -eu
umask 077
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
state_dir="${CD_WEB_RUNTIME:-${XDG_STATE_HOME:-$HOME/.local/state}/captains-deck-web}"
mkdir -p "$state_dir"
exec python3 "$project_dir/serve.py" managed "$@" >> "$state_dir/server.log" 2>&1
