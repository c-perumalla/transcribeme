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

# Honour HOST/PORT from .env too, so the file the app reads and the file you
# edit are the same one. An explicit shell variable still wins.
env_value() { sed -n "s/^$1=[[:space:]]*//p" .env 2>/dev/null | tail -1 | tr -d '"'"'"' \r'; }
HOST="${HOST:-$(env_value HOST)}"; HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-$(env_value PORT)}"; PORT="${PORT:-8000}"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use by:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/  /'
  echo "Set a different one, e.g.  PORT=8010 ./run.sh"
  exit 1
fi
echo "TranscribeMe on http://${HOST}:${PORT}"
exec ./.venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
