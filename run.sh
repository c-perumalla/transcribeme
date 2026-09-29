#!/usr/bin/env bash
# macOS launcher. Windows: double-click run.cmd instead.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: brew install uv   (or: curl -LsSf https://astral.sh/uv/install.sh | sh)"
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required: brew install ffmpeg"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "No .env found — copying .env.example. Fill in your tokens."
  cp .env.example .env
fi

# uv installs Python 3.13 and the locked dependencies on first run.
# HOST/PORT come from .env (or the shell); the launcher checks the port.
exec uv run --frozen transcribeme serve "$@"
