#!/usr/bin/env bash
set -euo pipefail

# Move to the script directory
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1

export PYTHONIOENCODING=utf-8

# Find a python executable
if command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
else
  echo "Python is not installed or not on PATH."
  exit 1
fi

HEALTH_URL='http://127.0.0.1:4381/api/health'
APP_URL='http://127.0.0.1:4381'

# Check service health; if healthy open browser and exit
if "$PYTHON_CMD" -c "import json,urllib.request; d=json.load(urllib.request.urlopen('$HEALTH_URL',timeout=2)); assert d.get('service')=='research-swarm'" >/dev/null 2>&1; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  else
    "$PYTHON_CMD" -m webbrowser -t "$APP_URL" >/dev/null 2>&1 || true
  fi
  exit 0
fi

# Ensure required Python packages are installed
if ! "$PYTHON_CMD" -c "import pypdf" >/dev/null 2>&1; then
  echo "Installing Python dependencies from requirements.txt..."
  if ! "$PYTHON_CMD" -m pip install -r requirements.txt; then
    echo "Dependency installation failed. Please check the message above."
    read -n1 -r -p "Press any key to continue..." _ || true
    exit 1
  fi
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
  "$PYTHON_CMD" -m webbrowser -t "$APP_URL" >/dev/null 2>&1 || true
fi

# Prefer uvicorn ASGI server when requested via UV environment variable.
if [ "${USE_UV:-}" = "1" ]; then
  # Try to run uvicorn with the ASGI app we added. Fall back to builtin server on failure.
  if "$PYTHON_CMD" -c "import uvicorn" >/dev/null 2>&1; then
    exec "$PYTHON_CMD" -m uvicorn research_swarm.asgi:app --host 127.0.0.1 --port 4381
  else
    echo "uvicorn not installed; falling back to builtin HTTP server"
  fi
fi

# Start the research_swarm server (foreground)
exec "$PYTHON_CMD" -X utf8 -m research_swarm --port 4381

# If exec returns, pause so the user can see output
read -n1 -r -p "Press any key to continue..." _ || true
