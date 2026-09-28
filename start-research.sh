#!/usr/bin/env bash
set -euo pipefail

# Move to the script directory
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1

export PYTHONIOENCODING=utf-8

# Use uv for the project environment and dependency management.
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed or not on PATH. Install uv to start research-swarm."
  exit 1
fi

UV_RUN=(uv run --locked)
UV_PYTHON=(uv run --locked --no-sync python)

HEALTH_URL='http://127.0.0.1:4381/api/health'
APP_URL='http://127.0.0.1:4381'

# Check service health; if healthy open browser and exit.
if "${UV_PYTHON[@]}" -c "import json,urllib.request; d=json.load(urllib.request.urlopen('$HEALTH_URL',timeout=2)); assert d.get('service')=='research-swarm'" >/dev/null 2>&1; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  else
    "${UV_PYTHON[@]}" -m webbrowser -t "$APP_URL" >/dev/null 2>&1 || true
  fi
  exit 0
fi

# Create/update the locked uv project environment before starting the server.
echo "Syncing Python dependencies with uv..."
if ! uv sync --locked; then
  echo "Dependency installation failed. Please check the message above."
  read -n1 -r -p "Press any key to continue..." _ || true
  exit 1
fi

# Build frontend if needed
if [ ! -f "dist/index.html" ]; then
  if ! command -v pnpm >/dev/null 2>&1; then
    echo "pnpm is not installed or not on PATH; cannot build frontend."
    exit 1
  fi
  pnpm install --frozen-lockfile || { echo "pnpm install failed"; exit 1; }
  pnpm run build || { echo "pnpm run build failed"; exit 1; }
fi

# Open the browser
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$APP_URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$APP_URL" >/dev/null 2>&1 || true
else
  "${UV_PYTHON[@]}" -m webbrowser -t "$APP_URL" >/dev/null 2>&1 || true
fi

# Prefer uvicorn ASGI server when requested via UV environment variable.
if [ "${USE_UV:-}" = "1" ]; then
  # Install/use the optional ASGI dependencies inside the uv project environment.
  if uv run --locked --extra server python -c "import uvicorn" >/dev/null 2>&1; then
    exec uv run --locked --extra server python -m uvicorn research_swarm.asgi:app --host 127.0.0.1 --port 4381
  else
    echo "uvicorn is unavailable; falling back to builtin HTTP server"
  fi
fi

# Start the research_swarm server (foreground)
exec "${UV_RUN[@]}" python -X utf8 -m research_swarm --port 4381

# If exec returns, pause so the user can see output
read -n1 -r -p "Press any key to continue..." _ || true
