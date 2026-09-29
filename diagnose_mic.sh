#!/usr/bin/env bash
# Tests audio capture directly with ffmpeg, bypassing the app, so permission
# problems and device quirks show their real error message.
#   ./diagnose_mic.sh                 # first input device
#   ./diagnose_mic.sh "MacBook Pro Microphone"
set -uo pipefail
cd "$(dirname "$0")"

echo "The microphone grant belongs to the app that launched THIS shell:"
ps -o comm= -p "$PPID" 2>/dev/null | sed 's/^/  parent: /'
echo "  (If that is VS Code / Cursor / iTerm, THAT app needs the grant.)"
echo

exec uv run --frozen transcribeme doctor --offline --mic "${1:-}"
