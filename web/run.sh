#!/usr/bin/env bash
# Run the Captain's Deck bridge in the foreground.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python3 bridge.py
