#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

if [[ ! -f .env ]]; then
  echo "No .env found — copying .env.example. Fill in your tokens."
  cp .env.example .env
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
echo "TranscribeMe on http://${HOST}:${PORT}"
exec ./.venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
